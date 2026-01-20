import asyncio
import base64
from datetime import datetime
import gc
import json
import pickle  # nosec B403
from typing import Generic

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from cwagent.ais.browser.browser import Browser
from cwagent.ais.browser.context import BrowserContext
from cwagent.ais.controller.models import ActionModel, ActionResult
from cwagent.ais.controller.views import Context, Controller
from logging_config import get_agent_logger
from config import WS_ENDPOINT

logger = get_agent_logger()


class Agent(Generic[Context]):
    def __init__(
        self, browser: Browser | None = None, test_uuid: str = "", auth_token: str = "", test_context: dict = None, step_callback=None
    ):  # nosec B107
        self.auth_token = auth_token
        self.test_uuid = test_uuid
        self.ws_connect = None

        self.action_result = None
        self.is_done = False
        self.is_stopping = False

        self.current_step = 0
        self.step_callback = step_callback

        self.checkpoint_data = None
        self._run_finished_event = asyncio.Event()

        self.controller: Controller[Context] = Controller()

        self.browser = browser or Browser()
        self.browser_context = BrowserContext(
            browser=self.browser, config=self.browser.config.new_context_config
        )

        self.test_context = test_context or {}

        self.result_images = []
        logger.info(
            "[AGENT_INIT] Agent initialized | test_uuid=%s | has_browser=%s",
            self.test_uuid,
            browser is not None,
        )
    
    async def stop(self):
        logger.info("[AGENT_STOP] User requested stop. Sending signal to Server")
        self.is_stopping = True
        if self.ws_connect:
            try:
                await self.ws_connect.send(json.dumps({
                    "status": "cancelled",
                    "reason": "user clicked stop",
                }))
            # except ConnectionClosed:
            #     logger.warning("[AGENT_STOP] Connection closed while sending stop signal.")
            except Exception as e:
                logger.error(f"[AGENT_STOP_ERROR] Error sending stop signal: {e}", exc_info=True)

    async def run(self) -> None:
        logger.info("[AGENT_START] Starting Agent run | test_uuid=%s", self.test_uuid)

        # Establish WebSocket connection
        ws_url = f"{WS_ENDPOINT}/ws/agent_pool"
        logger.info("[WS_CONNECT] Connecting to WebSocket | url=%s", ws_url)
        self.ws_connect = await websockets.connect(
            ws_url,
            ping_interval=3000,
            ping_timeout=3000,
            additional_headers=[("Authorization", f"Bearer {self.auth_token}")],
            max_size=None,
        )
        logger.info("[WS_CONNECTED] WebSocket connection established")

        # Send initialization messages
        init_msg = {
            "type": "init",
            "payload": self.test_context,
        }
        logger.debug("[WS_SEND] Sending init message | data=%s", init_msg)
        
        # send init status
        await self.ws_connect.send(json.dumps(init_msg))

        while True:
            # nhận về phản hồi từ server
            respone_raw = await self.ws_connect.recv()
            respone = json.loads(respone_raw)
            if respone.get("status", None) == "Done":
                logger.info("[WS_INIT] Server initialized successfully. Proceeding to RUN.")
                break
            else:
                logger.error("[WS_INIT_FAIL] Agent already existed on Server.")
                raise RuntimeError("Initialization failed: Agent session already exists (Status: Existed).")

        # send run status
        run_msg = {"type": "run"}
        logger.debug("[WS_SEND] Sending run message | data=%s", run_msg)
        await self.ws_connect.send(json.dumps(run_msg))

        try:
            logger.info("[WS_LOOP] Starting WebSocket message loop")
            while True:
                if self.is_stopping:
                    try:
                        logger.info("[WS_STOP_WAIT] Waiting for server response during stopping")
                        
                        message_raw = await asyncio.wait_for(self.ws_connect.recv(), timeout=15.0)
                    except asyncio.TimeoutError:
                        logger.warning("[WS_STOP_WAIT] Timeout waiting for server response during stopping")
                        break
                else:
                    # nhận về phản hồi từ server
                    message_raw = await self.ws_connect.recv()
                logger.debug("[WS_WAIT] Waiting for incoming message")
                message = json.loads(message_raw)
                msg_type = message.get("type", "unknown")

                logger.info(
                    "[WS_RECV] Received message | type=%s | data=%s", msg_type, message
                )
                status = message.get("status", None)
                logger.info("[WS_STATUS] Processing message with status=%s", status)

                if status == "request_state":
                    logger.info("[STATE_REQ] Server requesting browser state")
                    state = await self.browser_context.get_state()
                    state_data: bytes = pickle.dumps(
                        state, protocol=pickle.HIGHEST_PROTOCOL
                    )
                    state_size = len(state_data)
                    logger.info(
                        "[STATE_SEND] Sending browser state | size_bytes=%d", state_size
                    )
                    await self.ws_connect.send(state_data)

                if status == "server_actions":
                    actions_data = message.get("data", None)
                    action_count = len(actions_data) if actions_data else 0
                    logger.info(
                        "[ACTION_REQ] Server requesting actions execution | "
                        "action_count=%d | actions=%s",
                        action_count,
                        actions_data,
                    )
                    await self.multi_act(actions_data)
   
                if status == "request_action_results":
                    result_count = len(self.action_result) if self.action_result else 0
                    logger.info(
                        "[RESULT_REQ] Server requesting action results | "
                        "result_count=%d | results=%s",
                        result_count,
                        self.action_result,
                    )
                    response_data = {"action_results": self.action_result}
                    logger.debug(
                        "[WS_SEND] Sending action results | data=%s", response_data
                    )
                    await self.ws_connect.send(json.dumps(response_data))

                if status == "testcase_done":
                    logger.info("[TESTCASE_DONE] Server signaling test completion")
                    break
        except ConnectionClosedOK:
            logger.info("[AGENT_INFO] WebSocket connection closed normally (1000 OK).")
        except ConnectionClosed as e:
            logger.info(f"[AGENT_INFO] WebSocket connection closed: {e}")
        except Exception as e:
            logger.error(
                "[AGENT_ERROR] Exception during agent run: %s", str(e), exc_info=True
            )
        
        finally:
            logger.info(
                "[AGENT_CLEANUP] Testcase run completed, closing WebSocket connection"
            )
            await self.ws_connect.close()
            await self.close()
            self._run_finished_event.set()

        logger.info(
            "[AGENT_END] Agent run completed | result_images_count=%d",
            len(self.result_images),
        )
        return self.result_images, self.action_result

    async def close(self):
        logger.info("[CLEANUP] Starting agent cleanup")
        if self.browser_context:
            logger.debug("[CLEANUP] Closing browser context")
            await self.browser_context.close()
        if self.browser:
            logger.debug("[CLEANUP] Closing browser")
            await self.browser.close()
        logger.debug("[CLEANUP] Running garbage collection")
        gc.collect()
        logger.info("[CLEANUP] Agent cleanup completed")

    async def multi_act(self, actions: list[ActionModel]) -> list[ActionResult]:
        results = []
        action_count = len(actions) if actions else 0
        logger.info(
            "[MULTI_ACT] Starting multi-action execution | action_count=%d",
            action_count,
        )

        await self.browser_context.remove_highlights()
        page = await self.browser_context.get_current_page()
        logger.debug("[MULTI_ACT] Browser context prepared, highlights removed")

        for i, action in enumerate(actions):
            logger.info(
                "[ACTION_EXEC] Executing action %d/%d | action_type=%s | data=%s",
                i + 1,
                action_count,
                getattr(action, "action_type", "unknown"),
                action,
            )

            action_status = await self.controller.act(action, self.browser_context)
            results.append(action_status.model_dump())

            logger.info(
                "[ACTION_RESULT] Action %d completed | success=%s | error=%s",
                i + 1,
                not action_status.error,
                action_status.error,
            )

            # Take screenshot after action
            raw = await page.screenshot(full_page=True, animations="disabled")
            screenshot_b64 = base64.b64encode(raw).decode("utf-8")
            self.result_images.append(screenshot_b64)
            logger.debug(
                "[SCREENSHOT] Screenshot captured after action %d | size_bytes=%d",
                i + 1,
                len(raw),
            )

            if action_status.is_done or action_status.error:
                logger.info(f"=-==-=-=-==-=-= stopping at action {i+1} =-==-=-=-==-=-=")
                logger.warning(
                    "[ACTION_STOP] ============ Stopping execution due to completion or error ============== | "
                    "is_done=%s | error=%s",
                    action_status.is_done,
                    action_status.error,
                )
                self.is_done = True
                # await self.close()
                break

        logger.info(
            "[MULTI_ACT] Multi-action execution completed | results_count=%d",
            len(results),
        )
        self.action_result = results
    
    async def _handle_restore_session(self, data: dict):
        """
        Reload cookies and local storage in your browser
        can be further expanded based on your needs (cookies, localStorage, sessionStorage, etc.)
        """
        # cookies = data.get("cookies", [])
        # local_storage = data.get("local_storage", {})
        current_url = data.get("current_url")
        page = await self.browser_context.get_current_page()
        await page.goto(current_url, wait_until="commit", timeout=30000)


