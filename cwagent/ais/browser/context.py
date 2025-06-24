import asyncio
import base64
import gc
import io
import re
import time
import uuid
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import BrowserContext as PlaywrightBrowserContext
from playwright.async_api import (
    ElementHandle,
    FrameLocator,
    Page,
)
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING: from cwagent.ais.browser.browser import Browser
from cwagent.ais.browser.views import (
    BrowserError,
    BrowserState,
    TabInfo,
    URLNotAllowedError,
)
from cwagent.ais.dom.views import DomService
from cwagent.ais.dom.models import DOMElementNode, SelectorMap


class BrowserContextConfig(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='ignore',
        populate_by_name=True,
        from_attributes=True,
        validate_assignment=True,
        revalidate_instances='subclass-instances',
    )

    minimum_wait_page_load_time: float = 0.25
    wait_for_network_idle_page_load_time: float = 0.5
    maximum_wait_page_load_time: float = 5
    wait_between_actions: float = 0.2

    disable_security: bool = False
    no_viewport: Optional[bool] = None

    user_agent: str = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36  (KHTML, like Gecko) Chrome/85.0.4183.102 Safari/537.36')

    highlight_elements: bool = True
    viewport_expansion: int = 0
    allowed_domains: list[str] | None = None
    include_dynamic_attributes: bool = True
    http_credentials: dict[str, str] | None = None

    keep_alive: bool = Field(default=False, alias='_force_keep_context_alive')
    is_mobile: bool | None = None
    has_touch: bool | None = None
    permissions: list[str] | None = None


class BrowserSession:
    def __init__(self, context: PlaywrightBrowserContext, cached_state: BrowserState | None = None):
        init_script = """
			(() => {
				if (!window.getEventListeners) {
					window.getEventListeners = function (node) {
						return node.__listeners || {};
					};

					// Save the original addEventListener
					const originalAddEventListener = Element.prototype.addEventListener;

					const eventProxy = {
						addEventListener: function (type, listener, options = {}) {
							// Initialize __listeners if not exists
							const defaultOptions = { once: false, passive: false, capture: false };
							if(typeof options === 'boolean') {
								options = { capture: options };
							}
							options = { ...defaultOptions, ...options };
							if (!this.__listeners) {
								this.__listeners = {};
							}

							// Initialize array for this event type if not exists
							if (!this.__listeners[type]) {
								this.__listeners[type] = [];
							}


							// Add the listener to __listeners
							this.__listeners[type].push({
								listener: listener,
								type: type,
								...options
							});

							// Call original addEventListener using the saved reference
							return originalAddEventListener.call(this, type, listener, options);
						}
					};

					Element.prototype.addEventListener = eventProxy.addEventListener;
				}
			})()
			"""
        self.active_tab = None
        self.context = context
        self.cached_state = cached_state
        self.context.on('page', lambda page: page.add_init_script(init_script))


@dataclass
class BrowserContextState:
    target_id: str | None = None


class BrowserContext:
    def __init__(self, browser: 'Browser', config: BrowserContextConfig | None = None, state: Optional[BrowserContextState] = None):
        self.context_id = str(uuid.uuid4())

        self.config = config or BrowserContextConfig(**(browser.config.model_dump() if browser.config else {}))
        self.browser = browser

        self.state = state or BrowserContextState()

        self.session: BrowserSession | None = None
        self.active_tab: Page | None = None

    async def __aenter__(self):
        """Async context manager entry"""
        await self._initialize_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()

    async def close(self):
        try:
            if self.session is None:
                return

            if self._page_event_handler and self.session.context:
                self.session.context.remove_listener('page', self._page_event_handler)
                self._page_event_handler = None

            if not self.config.keep_alive:
                await self.session.context.close()

        finally:
            self.active_tab = None
            self.session = None
            self._page_event_handler = None

    def __del__(self):
        if not self.config.keep_alive and self.session is not None:
            if hasattr(self.session.context, '_impl_obj'):
                asyncio.run(self.session.context._impl_obj.close())

            self.session = None
            gc.collect()

    async def _initialize_session(self):
        playwright_browser = await self.browser.get_playwright_browser()
        context = await self._create_context(playwright_browser)
        self._page_event_handler = None

        pages = context.pages

        self.session = BrowserSession(context=context, cached_state=None)

        active_page = None

        if not active_page:
            if (
                    pages
                    and pages[0].url
                    and not pages[0].url.startswith('chrome://')
                    and not pages[0].url.startswith('chrome-extension://')
            ):
                active_page = pages[0]
            else:
                active_page = await context.new_page()
                await active_page.goto('about:blank')

        await active_page.bring_to_front()
        await active_page.wait_for_load_state('load')

        self.active_tab = active_page

        return self.session

    async def get_session(self) -> BrowserSession:
        """Lazy initialization of the browser and related components"""
        if self.session is None:
            return await self._initialize_session()
        return self.session

    async def get_current_page(self) -> Page:
        session = await self.get_session()
        return await self._get_current_page(session)

    async def _create_context(self, browser: PlaywrightBrowser):
        if self.browser.config.browser_binary_path and len(browser.contexts) > 0:
            context = browser.contexts[0]
        else:
            context = await browser.new_context(
                no_viewport=True,
                user_agent=self.config.user_agent,
                java_script_enabled=True,
                bypass_csp=self.config.disable_security,
                ignore_https_errors=self.config.disable_security,
                http_credentials=self.config.http_credentials,
                is_mobile=self.config.is_mobile,
                has_touch=self.config.has_touch,
                permissions=self.config.permissions,
            )

        await context.add_init_script(
            """
            // Webdriver property
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // Languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US']
            });

            // Plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // Chrome runtime
            window.chrome = { runtime: {} };

            // Permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            (function () {
                const originalAttachShadow = Element.prototype.attachShadow;
                Element.prototype.attachShadow = function attachShadow(options) {
                    return originalAttachShadow.call(this, { ...options, mode: "open" });
                };
            })();
            """
        )

        return context

    async def _wait_for_stable_network(self):
        page = await self.get_current_page()

        pending_requests = set()
        last_activity = asyncio.get_event_loop().time()

        RELEVANT_RESOURCE_TYPES = {'document', 'stylesheet', 'image', 'font', 'script', 'iframe'}
        RELEVANT_CONTENT_TYPES = {'text/html', 'text/css', 'application/javascript', 'image/', 'font/', 'application/json'}
        IGNORED_URL_PATTERNS = {
            'analytics', 'tracking', 'telemetry', 'beacon', 'metrics',
            'doubleclick', 'adsystem', 'adserver', 'advertising',
            'facebook.com/plugins', 'platform.twitter', 'linkedin.com/embed',
            'livechat', 'zendesk', 'intercom', 'crisp.chat', 'hotjar',
            'push-notifications', 'onesignal', 'pushwoosh',
            'heartbeat', 'ping', 'alive',
            'webrtc', 'rtmp://', 'wss://',
            'cloudfront.net', 'fastly.net'
        }

        async def on_request(request):
            if request.resource_type not in RELEVANT_RESOURCE_TYPES: return
            if request.resource_type in {'websocket', 'media', 'eventsource', 'manifest', 'other'}: return

            url = request.url.lower()
            if any(pattern in url for pattern in IGNORED_URL_PATTERNS): return
            if url.startswith(('data:', 'blob:')): return

            headers = request.headers
            if headers.get('purpose') == 'prefetch' or headers.get('sec-fetch-dest') in ['video', 'audio']: return

            nonlocal last_activity
            pending_requests.add(request)
            last_activity = asyncio.get_event_loop().time()

        async def on_response(response):
            request = response.request
            if request not in pending_requests: return

            content_type = response.headers.get('content-type', '').lower()

            if any(
                    t in content_type
                    for t in [ 'streaming', 'video', 'audio', 'webm', 'mp4', 'event-stream', 'websocket', 'protobuf']
            ):
                pending_requests.remove(request)
                return

            if not any(ct in content_type for ct in RELEVANT_CONTENT_TYPES):
                pending_requests.remove(request)
                return

            content_length = response.headers.get('content-length')
            if content_length and int(content_length) > 5 * 1024 * 1024:
                pending_requests.remove(request)
                return

            nonlocal last_activity
            pending_requests.remove(request)
            last_activity = asyncio.get_event_loop().time()


        page.on('request', on_request)
        page.on('response', on_response)

        try:
            start_time = asyncio.get_event_loop().time()
            while True:
                await asyncio.sleep(0.1)
                now = asyncio.get_event_loop().time()
                if len(pending_requests) == 0 and (
                        now - last_activity) >= self.config.wait_for_network_idle_page_load_time:
                    break
                if now - start_time > self.config.maximum_wait_page_load_time:
                    break

        finally:
            page.remove_listener('request', on_request)
            page.remove_listener('response', on_response)

    async def _wait_for_page_and_frames_load(self, timeout_overwrite: float | None = None):
        start_time = time.time()

        try:
            await self._wait_for_stable_network()

            page = await self.get_current_page()
            await self._check_and_handle_navigation(page)
        except URLNotAllowedError as e:
            raise e
        except Exception:
            pass

        elapsed = time.time() - start_time
        remaining = max((timeout_overwrite or self.config.minimum_wait_page_load_time) - elapsed, 0)

        if remaining > 0: await asyncio.sleep(remaining)

    def _is_url_allowed(self, url: str) -> bool:
        if not self.config.allowed_domains: return True

        try:
            from urllib.parse import urlparse

            parsed_url = urlparse(url)
            domain = parsed_url.netloc.lower()

            if url == 'about:blank': return True

            if ':' in domain:
                domain = domain.split(':')[0]

            return any(
                domain == allowed_domain.lower() or domain.endswith('.' + allowed_domain.lower())
                for allowed_domain in self.config.allowed_domains
            )
        except Exception as e:
            return False

    async def _check_and_handle_navigation(self, page: Page) -> None:
        if not self._is_url_allowed(page.url):
            await self.go_back()
            raise URLNotAllowedError(f'Navigation to non-allowed URL: {page.url}')

    async def go_back(self):
        page = await self.get_current_page()
        await page.go_back(timeout=10, wait_until='domcontentloaded')

    async def get_state(self) -> BrowserState:
        """Get the current state of the browser"""
        await self._wait_for_page_and_frames_load()
        session = await self.get_session()
        session.cached_state = await self._update_state()

        return session.cached_state

    async def _update_state(self, focus_element: int = -1) -> BrowserState:
        """Update and return state."""
        session = await self.get_session()

        try:
            page = await self.get_current_page()
            await page.evaluate('1')
        except Exception as e:
            pages = session.context.pages
            if pages:
                self.state.target_id = None
                page = await self._get_current_page(session)
            else:
                raise BrowserError('Browser closed: no valid pages available')

        try:
            await self.remove_highlights()
            dom_service = DomService(page)
            content = await dom_service.get_clickable_elements(
                focus_element=focus_element,
                viewport_expansion=self.config.viewport_expansion,
                highlight_elements=self.config.highlight_elements,
            )

            tabs_info = await self.get_tabs_info()

            screenshot_b64 = await self.take_screenshot()
            pixels_above, pixels_below = await self.get_scroll_info(page)

            self.current_state = BrowserState(
                element_tree=content.element_tree,
                selector_map=content.selector_map,
                url=page.url,
                title=await page.title(),
                tabs=tabs_info,
                screenshot=screenshot_b64,
                pixels_above=pixels_above,
                pixels_below=pixels_below,
            )

            return self.current_state
        except Exception as e:
            if hasattr(self, 'current_state'):
                return self.current_state
            raise

    async def take_screenshot(self, full_page: bool = False, add_url_banner: bool = True) -> str:
        page = await self.get_current_page()

        await page.bring_to_front()
        await page.wait_for_load_state()

        png_bytes: bytes = await page.screenshot(
            full_page=full_page,
            animations='disabled',
        )

        if not add_url_banner:
            return base64.b64encode(png_bytes).decode()

        with Image.open(io.BytesIO(png_bytes)).convert("RGBA") as shot:
            w, h = shot.size
            banner_h = 42
            banner = Image.new("RGBA", (w, banner_h), color=(0, 0, 0, 255))

            draw = ImageDraw.Draw(banner)
            try:
                font = ImageFont.truetype("arial.ttf", 20)
            except OSError:
                font = ImageFont.load_default()

            url = page.url[:250]
            bbox = draw.textbbox((0, 0), url, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            draw.text(((w - text_w) / 2, (banner_h - text_h) / 2),
                      url, font=font, fill=(255, 255, 255, 255))

            out = Image.new("RGBA", (w, banner_h + h))
            out.paste(banner, (0, 0))
            out.paste(shot, (0, banner_h))

            buf = io.BytesIO()
            out.save(buf, format="PNG", optimize=True)
            png_bytes = buf.getvalue()


        return base64.b64encode(png_bytes).decode()

    async def remove_highlights(self):
        """
        Removes all highlight overlays and labels created by the highlightElement function.
        Handles cases where the page might be closed or inaccessible.
        """
        try:
            page = await self.get_current_page()
            await page.evaluate(
                """
                try {
                    // Remove the highlight container and all its contents
                    const container = document.getElementById('playwright-highlight-container');
                    if (container) {
                        container.remove();
                    }

                    // Remove highlight attributes from elements
                    const highlightedElements = document.querySelectorAll('[browser-user-highlight-id^="playwright-highlight-"]');
                    highlightedElements.forEach(el => {
                        el.removeAttribute('browser-user-highlight-id');
                    });
                } catch (e) {
                    console.error('Failed to remove highlights:', e);
                }
                """
            )
        except Exception as e:
            pass

    @classmethod
    def _convert_simple_xpath_to_css_selector(cls, xpath: str) -> str:
        if not xpath: return ''

        xpath = xpath.lstrip('/')
        parts = xpath.split('/')
        css_parts = []

        for part in parts:
            if not part: continue

            if ':' in part and '[' not in part:
                base_part = part.replace(':', r'\:')
                css_parts.append(base_part)
                continue

            if '[' in part:
                base_part = part[: part.find('[')]
                if ':' in base_part: base_part = base_part.replace(':', r'\:')
                index_part = part[part.find('['):]

                indices = [i.strip('[]') for i in index_part.split(']')[:-1]]

                for idx in indices:
                    try:
                        if idx.isdigit():
                            index = int(idx) - 1
                            base_part += f':nth-of-type({index + 1})'
                        elif idx == 'last()':
                            base_part += ':last-of-type'
                        elif 'position()' in idx:
                            if '>1' in idx:
                                base_part += ':nth-of-type(n+2)'
                    except ValueError:
                        continue

                css_parts.append(base_part)
            else:
                css_parts.append(part)

        base_selector = ' > '.join(css_parts)
        return base_selector

    @classmethod
    def _enhanced_css_selector_for_element(cls, element: DOMElementNode, include_dynamic_attributes: bool = True) -> str:
        try:
            css_selector = cls._convert_simple_xpath_to_css_selector(element.xpath)

            if 'class' in element.attributes and element.attributes['class'] and include_dynamic_attributes:
                valid_class_name_pattern = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_-]*$')
                classes = element.attributes['class'].split()
                for class_name in classes:
                    if not class_name.strip(): continue
                    if valid_class_name_pattern.match(class_name):
                        css_selector += f'.{class_name}'
                    else: continue

            SAFE_ATTRIBUTES = {
                'id', 'name', 'type', 'placeholder', 'aria-label', 'aria-labelledby', 'aria-describedby', 'role',
                'for', 'autocomplete', 'required', 'readonly', 'alt', 'title', 'src', 'href', 'target'
            }

            if include_dynamic_attributes:
                dynamic_attributes = {'data-id', 'data-qa', 'data-cy', 'data-testid'}
                SAFE_ATTRIBUTES.update(dynamic_attributes)

            for attribute, value in element.attributes.items():
                if attribute == 'class': continue
                if not attribute.strip(): continue
                if attribute not in SAFE_ATTRIBUTES: continue

                safe_attribute = attribute.replace(':', r'\:')

                if value == '': css_selector += f'[{safe_attribute}]'
                elif any(char in value for char in '"\'<>`\n\r\t'):
                    collapsed_value = re.sub(r'\s+', ' ', value).strip()
                    safe_value = collapsed_value.replace('"', '\\"')
                    css_selector += f'[{safe_attribute}*="{safe_value}"]'
                else:
                    css_selector += f'[{safe_attribute}="{value}"]'

            return css_selector

        except Exception:
            tag_name = element.tag_name or '*'
            return f"{tag_name}[highlight_index='{element.highlight_index}']"

    async def get_locate_element(self, element: DOMElementNode) -> Optional[ElementHandle]:
        current_frame = await self.get_current_page()

        parents: list[DOMElementNode] = []
        current = element
        while current.parent is not None:
            parent = current.parent
            parents.append(parent)
            current = parent

        parents.reverse()

        iframes = [item for item in parents if item.tag_name == 'iframe']
        for parent in iframes:
            css_selector = self._enhanced_css_selector_for_element(
                parent,
                include_dynamic_attributes=self.config.include_dynamic_attributes,
            )
            current_frame = current_frame.frame_locator(css_selector)

        css_selector = self._enhanced_css_selector_for_element(
            element, include_dynamic_attributes=self.config.include_dynamic_attributes
        )

        try:
            if isinstance(current_frame, FrameLocator):
                element_handle = await current_frame.locator(css_selector).element_handle()
                return element_handle
            else:
                element_handle = await current_frame.query_selector(css_selector)
                if element_handle:
                    await element_handle.scroll_into_view_if_needed()
                    return element_handle
                return None
        except Exception as e:
            return None

    async def get_locate_element_by_xpath(self, xpath: str) -> Optional[ElementHandle]:
        current_frame = await self.get_current_page()

        try:
            element_handle = await current_frame.query_selector(f'xpath={xpath}')
            if element_handle:
                await element_handle.scroll_into_view_if_needed()
                return element_handle
            return None
        except Exception as e:
            return None

    async def get_locate_element_by_css_selector(self, css_selector: str) -> Optional[ElementHandle]:
        current_frame = await self.get_current_page()

        try:
            element_handle = await current_frame.query_selector(css_selector)
            if element_handle:
                await element_handle.scroll_into_view_if_needed()
                return element_handle
            return None
        except Exception as e:
            return None

    async def get_locate_element_by_text(self, text: str, nth: Optional[int] = 0, element_type: Optional[str] = None) -> Optional[ElementHandle]:
        current_frame = await self.get_current_page()
        try:
            selector = f'{element_type or "*"}:text("{text}")'
            elements = await current_frame.query_selector_all(selector)
            elements = [el for el in elements if await el.is_visible()]

            if not elements:
                return None

            if nth is not None:
                if 0 <= nth < len(elements):
                    element_handle = elements[nth]
                else:
                    return None
            else:
                element_handle = elements[0]

            await element_handle.scroll_into_view_if_needed()
            return element_handle
        except Exception as e:
            return None

    async def _input_text_element_node(self, element_node: DOMElementNode, text: str):
        try:
            element_handle = await self.get_locate_element(element_node)
            if element_handle is None: raise BrowserError(f'Element: {repr(element_node)} not found')

            try:
                await element_handle.wait_for_element_state('stable', timeout=1000)
                await element_handle.scroll_into_view_if_needed(timeout=1000)
            except Exception:
                pass

            tag_handle = await element_handle.get_property('tagName')
            tag_name = (await tag_handle.json_value()).lower()
            is_contenteditable = await element_handle.get_property('isContentEditable')
            readonly_handle = await element_handle.get_property('readOnly')
            disabled_handle = await element_handle.get_property('disabled')

            readonly = await readonly_handle.json_value() if readonly_handle else False
            disabled = await disabled_handle.json_value() if disabled_handle else False

            if (await is_contenteditable.json_value() or tag_name == 'input') and not (readonly or disabled):
                await element_handle.evaluate('el => {el.textContent = ""; el.value = "";}')
                await element_handle.type(text, delay=5)
            else:
                await element_handle.fill(text)

        except Exception as e:
            raise BrowserError(f'Failed to input text into index {element_node.highlight_index}')

    async def _click_element_node(self, element_node: DOMElementNode) -> Optional[str]:
        page = await self.get_current_page()

        try:
            element_handle = await self.get_locate_element(element_node)

            if element_handle is None: raise Exception(f'Element: {repr(element_node)} not found')

            async def perform_click(click_func):
                await click_func()
                await page.wait_for_load_state()
                await self._check_and_handle_navigation(page)

            try:
                return await perform_click(lambda: element_handle.click(timeout=500))
            except URLNotAllowedError as e:
                raise e
            except Exception:
                try:
                    return await perform_click(lambda: page.evaluate('(el) => el.click()', element_handle))
                except URLNotAllowedError as e:
                    raise e
                except Exception as e:
                    raise Exception(f'Failed to click element: {str(e)}')

        except URLNotAllowedError as e:
            raise e
        except Exception as e:
            raise Exception(f'Failed to click element: {repr(element_node)}. Error: {str(e)}')

    async def get_tabs_info(self) -> list[TabInfo]:
        session = await self.get_session()

        tabs_info = []
        for page_id, page in enumerate(session.context.pages):
            try:
                tab_info = TabInfo(page_id=page_id, url=page.url, title=await asyncio.wait_for(page.title(), timeout=1))
            except asyncio.TimeoutError:
                tab_info = TabInfo(page_id=page_id, url='about:blank', title='ignore this tab and do not use it')
            tabs_info.append(tab_info)

        return tabs_info

    async def switch_to_tab(self, page_id: int) -> None:
        session = await self.get_session()
        pages = session.context.pages

        if page_id >= len(pages): raise BrowserError(f'No tab found with page_id: {page_id}')

        page = pages[page_id]

        if not self._is_url_allowed(page.url): raise BrowserError(f'Cannot switch to tab with non-allowed URL: {page.url}')

        self.active_tab = page
        await page.bring_to_front()
        await page.wait_for_load_state()

    async def create_new_tab(self, url: str | None = None) -> None:
        if url and not self._is_url_allowed(url):
            raise BrowserError(f'Cannot create new tab with non-allowed URL: {url}')

        session = await self.get_session()
        new_page = await session.context.new_page()

        self.active_tab = new_page
        await new_page.wait_for_load_state()

        if url:
            await new_page.goto(url)
            await self._wait_for_page_and_frames_load(timeout_overwrite=1)

    async def _get_current_page(self, session: BrowserSession) -> Page:
        pages = session.context.pages

        if self.active_tab and self.active_tab in session.context.pages and not self.active_tab.is_closed():
            return self.active_tab

        non_extension_pages = [
            page for page in pages if
            not page.url.startswith('chrome-extension://') and
            not page.url.startswith('chrome://')
        ]
        if non_extension_pages: return non_extension_pages[-1]

        try:
            return await session.context.new_page()
        except Exception:
            await self._initialize_session()
            page = await session.context.new_page()
            self.active_tab = page
            return page

    async def get_selector_map(self) -> SelectorMap:
        session = await self.get_session()
        if session.cached_state is None: return {}
        return session.cached_state.selector_map

    async def get_dom_element_by_index(self, index: int) -> DOMElementNode:
        selector_map = await self.get_selector_map()
        return selector_map[index]

    async def is_file_uploader(self, element_node: DOMElementNode, max_depth: int = 3, current_depth: int = 0) -> bool:
        if current_depth > max_depth: return False

        is_uploader = False

        if not isinstance(element_node, DOMElementNode): return False

        if element_node.tag_name == 'input':
            is_uploader = element_node.attributes.get('type') == 'file' or element_node.attributes.get('accept') is not None

        if is_uploader: return True

        if element_node.children and current_depth < max_depth:
            for child in element_node.children:
                if isinstance(child, DOMElementNode):
                    if await self.is_file_uploader(child, max_depth, current_depth + 1):
                        return True

        return False

    async def get_scroll_info(self, page: Page) -> tuple[int, int]:
        scroll_y = await page.evaluate('window.scrollY')
        viewport_height = await page.evaluate('window.innerHeight')
        total_height = await page.evaluate('document.documentElement.scrollHeight')
        pixels_above = scroll_y
        pixels_below = total_height - (scroll_y + viewport_height)
        return pixels_above, pixels_below

    async def wait_for_element(self, selector: str, timeout: float) -> None:
        page = await self.get_current_page()
        await page.wait_for_selector(selector, state='visible', timeout=timeout)
