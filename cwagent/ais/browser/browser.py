import asyncio
import gc
import socket
import subprocess
import sys

import httpx
from typing import Literal

import psutil
import requests
from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import Playwright, async_playwright
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from cwagent.ais.browser.chrome import (
    CHROME_ARGS,
    CHROME_DETERMINISTIC_RENDERING_ARGS,
    CHROME_DISABLE_SECURITY_ARGS,
    CHROME_HEADLESS_ARGS,
)
from cwagent.ais.browser.context import BrowserContext, BrowserContextConfig


class BrowserConfig(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='ignore',
        populate_by_name=True,
        from_attributes=True,
        validate_assignment=True,
        revalidate_instances='subclass-instances',
    )

    browser_class: Literal['chromium', 'firefox'] = 'chromium' # , 'webkit'
    browser_binary_path: str | None = Field(
        default=None,
        alias=AliasChoices('browser_instance_path', 'chrome_instance_path')
    )
    extra_browser_args: list[str] = Field(default_factory=list)

    headless: bool = False
    disable_security: bool = False
    deterministic_rendering: bool = False
    keep_alive: bool = Field(default=False, alias='_force_keep_browser_alive')

    new_context_config: BrowserContextConfig = Field(default_factory=BrowserContextConfig)


class Browser:
    def __init__(self, config: BrowserConfig | None = None):
        self.config = config or BrowserConfig()
        self.playwright: Playwright | None = None
        self.playwright_browser: PlaywrightBrowser | None = None

    async def new_context(self, config: BrowserContextConfig | None = None) -> BrowserContext:
        return BrowserContext(config=config or self.config, browser=self)

    async def get_playwright_browser(self) -> PlaywrightBrowser:
        if self.playwright_browser is None:
            return await self._init()

        return self.playwright_browser

    async def _init(self):
        playwright = await async_playwright().start()
        browser = await self._setup_browser(playwright)

        self.playwright = playwright
        self.playwright_browser = browser

        return self.playwright_browser

    async def _setup_user_provided_browser(self, playwright: Playwright) -> PlaywrightBrowser:
        if not self.config.browser_binary_path:
            raise ValueError('A browser_binary_path is required')

        assert self.config.browser_class == 'chromium', (
            'browser_binary_path only supports chromium browsers (make sure browser_class=chromium)'
        )

        try:
            response = requests.get('http://localhost:9222/json/version', timeout=2)
            if response.status_code == 200:
                browser_class = getattr(playwright, self.config.browser_class)
                browser = await browser_class.connect_over_cdp(
                    endpoint_url='http://localhost:9222',
                    timeout=20000,
                )
                return browser
        except requests.ConnectionError:
            print('🌎  No existing Chrome instance found, starting a new one')

        chrome_launch_cmd = [
            self.config.browser_binary_path,
            *{
                *CHROME_ARGS,
                *(CHROME_HEADLESS_ARGS if self.config.headless else []),
                *(CHROME_DISABLE_SECURITY_ARGS if self.config.disable_security else []),
                *(CHROME_DETERMINISTIC_RENDERING_ARGS if self.config.deterministic_rendering else []),
                *self.config.extra_browser_args,
            },
        ]
        self._chrome_subprocess = psutil.Process(
            subprocess.Popen(
                chrome_launch_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            ).pid
        )

        for _ in range(10):
            try:
                response = requests.get('http://localhost:9222/json/version', timeout=2)
                if response.status_code == 200:
                    break
            except requests.ConnectionError:
                pass
            await asyncio.sleep(1)

        try:
            browser_class = getattr(playwright, self.config.browser_class)
            browser = await browser_class.connect_over_cdp(
                endpoint_url='http://localhost:9222',
                timeout=20000,
            )
            return browser
        except Exception as e:
            raise RuntimeError(
                'To start chrome in Debug mode, you need to close all existing Chrome instances and try again otherwise we can not connect to the instance.'
            )

    async def _setup_builtin_browser(self, playwright: Playwright) -> PlaywrightBrowser:
        assert self.config.browser_binary_path is None, 'browser_binary_path should be None if trying to use the builtin browsers'

        if self.config.headless:
            screen_size = {'width': 1920, 'height': 1080}
            offset_x, offset_y = 0, 0
        else:
            if sys.platform == 'darwin':
                screen_size = {'width': 2560, 'height': 1664}

                try:
                    from AppKit import NSScreen
                    screen = NSScreen.mainScreen().frame()
                    screen_size = {'width': int(screen.size.width), 'height': int(screen.size.height)}
                except ImportError:
                    print('AppKit is not available. Make sure you are running this on macOS with pyobjc installed.')
                except Exception as e:
                    print(f'Error retrieving macOS screen resolution: {e}')

            else:
                screen_size = {'width': 1920, 'height': 1080}
                try:
                    from screeninfo import get_monitors
                    monitors = get_monitors()
                    if not monitors:
                        raise Exception('No monitors detected.')
                    monitor = monitors[0]
                    screen_size =  {'width': monitor.width, 'height': monitor.height}
                except ImportError:
                    print("screeninfo package not found. Install it using 'pip install screeninfo'.")
                except Exception as e:
                    print(f'Error retrieving screen resolution: {e}')

            if sys.platform == 'darwin':
                offset_x, offset_y = -4, 24
            elif sys.platform == 'win32':
                offset_x, offset_y = -8, 0
            else:
                offset_x, offset_y = 0, 0

        chrome_args = {
            *CHROME_ARGS,
            *(CHROME_HEADLESS_ARGS if self.config.headless else []),
            *(CHROME_DISABLE_SECURITY_ARGS if self.config.disable_security else []),
            *(CHROME_DETERMINISTIC_RENDERING_ARGS if self.config.deterministic_rendering else []),
            f'--window-position={offset_x},{offset_y}',
            f'--window-size={screen_size["width"]},{screen_size["height"]}',
            *self.config.extra_browser_args,
        }

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', 9222)) == 0:
                chrome_args.remove('--remote-debugging-port=9222')

        browser_class = getattr(playwright, self.config.browser_class)
        args = {
            'chromium': list(chrome_args),
            'firefox': [*{'-no-remote', *self.config.extra_browser_args}],
            'webkit': [*{ '--no-startup-window', *self.config.extra_browser_args}]
        }

        browser = await browser_class.launch(
            headless=self.config.headless,
            args=args[self.config.browser_class],
            handle_sigterm=False,
            handle_sigint=False,
        )
        return browser

    async def _setup_browser(self, playwright: Playwright) -> PlaywrightBrowser:
        if self.config.headless:
            print('⚠️ Headless mode is not recommended. Many sites will detect and block all headless browsers.')

        if self.config.browser_binary_path:
            return await self._setup_user_provided_browser(playwright)
        else:
            return await self._setup_builtin_browser(playwright)

    async def close(self):
        if self.config.keep_alive:
            return

        try:
            if self.playwright_browser:
                await self.playwright_browser.close()
                del self.playwright_browser
            if self.playwright:
                await self.playwright.stop()
                del self.playwright
            if chrome_proc := getattr(self, '_chrome_subprocess', None):
                for proc in chrome_proc.children(recursive=True):
                    proc.kill()
                chrome_proc.kill()

            gc.collect()
            clients = [obj for obj in gc.get_objects() if isinstance(obj, httpx.AsyncClient)]
            for client in clients:
                if not client.is_closed:
                    await client.aclose()

        except Exception as e:
            print(f'Failed to close browser properly: {e}')

        finally:
            self.playwright_browser = None
            self.playwright = None
            self._chrome_subprocess = None
            gc.collect()

    def __del__(self):
        if self.playwright_browser or self.playwright:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self.close())
            else:
                asyncio.run(self.close())
