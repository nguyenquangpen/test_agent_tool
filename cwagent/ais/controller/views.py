import asyncio
import enum
import json

from typing import Tuple, Any, Callable, Generic, Optional, Type, TypeVar, cast
from inspect import iscoroutinefunction, signature

from playwright.async_api import ElementHandle, Page

from pydantic import BaseModel, create_model

from cwagent.ais.controller.models import ActionResult
from cwagent.ais.browser.context import BrowserContext
from cwagent.ais.controller.models import (
    ClickElementAction,
    ClickElementBySelectorAction,
    ClickElementByTextAction,
    ClickElementByXpathAction,
    CloseTabAction,
    DoneAction,
    DragDropAction,
    GoToUrlAction,
    InputTextAction,
    NoParamsAction,
    OpenTabAction,
    Position,
    ScrollAction,
    SendKeysAction,
    SwitchTabAction,
    WaitForElementAction,
)
from cwagent.ais.controller.models import (
    ControllerActionModel,
    ActionRegistry,
    RegisteredAction,
)

Context = TypeVar('Context')


class Registry(Generic[Context]):
    def __init__(self, exclude_actions: list[str] | None = None):
        self.registry = ActionRegistry()
        self.exclude_actions = exclude_actions if exclude_actions is not None else []

    def _create_param_model(self, function: Callable) -> Type[BaseModel]:
        sig = signature(function)
        params = {
            name: (param.annotation, ... if param.default == param.empty else param.default)
            for name, param in sig.parameters.items()
            if name != 'browser' and name != 'page_extraction_llm' and name != 'available_file_paths'
        }

        return create_model(
            f'{function.__name__}_parameters',
            __base__=ControllerActionModel,
            **params,
        )

    def action(self, description: str, param_model: Optional[Type[BaseModel]] = None, domains: Optional[list[str]] = None, page_filter: Optional[Callable[[Any], bool]] = None):
        def decorator(func: Callable):
            if func.__name__ in self.exclude_actions:
                return func

            actual_param_model = param_model or self._create_param_model(func)

            if not iscoroutinefunction(func):

                async def async_wrapper(*args, **kwargs):
                    return await asyncio.to_thread(func, *args, **kwargs)

                async_wrapper.__signature__ = signature(func)
                async_wrapper.__name__ = func.__name__
                async_wrapper.__annotations__ = func.__annotations__
                wrapped_func = async_wrapper
            else:
                wrapped_func = func

            action = RegisteredAction(
                name=func.__name__,
                description=description,
                function=wrapped_func,
                param_model=actual_param_model,
                domains=domains,
                page_filter=page_filter,
            )
            self.registry.actions[func.__name__] = action
            return func

        return decorator

    async def execute_action(self, action_name: str, params: dict, browser: Optional[BrowserContext] = None) -> Any:
        if action_name not in self.registry.actions:
            raise ValueError(f'Action {action_name} not found')

        action = self.registry.actions[action_name]
        try:
            validated_params = action.param_model(**params)

            sig = signature(action.function)
            parameters = list(sig.parameters.values())
            is_pydantic = parameters and issubclass(parameters[0].annotation, BaseModel)
            parameter_names = [param.name for param in parameters]

            if 'browser' in parameter_names and not browser:
                raise ValueError(f'Action {action_name} requires browser but none provided.')

            extra_args = {}
            if 'browser' in parameter_names:
                extra_args['browser'] = browser
            if is_pydantic:
                return await action.function(validated_params, **extra_args)
            return await action.function(**validated_params.model_dump(), **extra_args)

        except Exception as e:
            raise RuntimeError(f'Error executing action {action_name}: {str(e)}') from e

class Controller(Generic[Context]):
    def __init__(self, exclude_actions: list[str] = [], output_model: Optional[Type[BaseModel]] = None):
        self.registry = Registry[Context](exclude_actions)
        if output_model is not None:
            class ExtendedOutputModel(BaseModel):
                success: bool = True
                data: output_model

            @self.registry.action(
                'Complete task - with return text and if the task is finished (success=True) or not yet  completely finished (success=False), because last step is reached',
                param_model=ExtendedOutputModel,
            )
            async def done(params: ExtendedOutputModel):
                output_dict = params.data.model_dump()
                for key, value in output_dict.items():
                    if isinstance(value, enum.Enum):
                        output_dict[key] = value.value

                return ActionResult(is_done=True, success=params.success, extracted_content=json.dumps(output_dict))
        else:
            @self.registry.action(
                'Complete task - with return text and if the task is finished (success=True) or not yet completely finished (success=False), because last step is reached',
                param_model=DoneAction
            )
            async def done(params: DoneAction):
                return ActionResult(is_done=True, success=params.success, extracted_content=params.text)

        @self.registry.action('Navigate to URL in the current tab', param_model=GoToUrlAction)
        async def go_to_url(params: GoToUrlAction, browser: BrowserContext):
            page = await browser.get_current_page()
            await page.goto(params.url)
            await page.wait_for_load_state()
            msg = f'🔗  Navigated to {params.url}'
            return ActionResult(extracted_content=msg, include_in_memory=True)

        @self.registry.action('Go back', param_model=NoParamsAction)
        async def go_back(_: NoParamsAction, browser: BrowserContext):
            await browser.go_back()
            msg = '🔙  Navigated back'
            return ActionResult(extracted_content=msg, include_in_memory=True)

        @self.registry.action('Wait for x seconds default 3')
        async def wait(seconds: int = 3):
            msg = f'🕒  Waiting for {seconds} seconds'
            await asyncio.sleep(seconds)
            return ActionResult(extracted_content=msg, include_in_memory=True)

        @self.registry.action('Wait for element to be visible', param_model=WaitForElementAction)
        async def wait_for_element(params: WaitForElementAction, browser: BrowserContext):
            try:
                await browser.wait_for_element(params.selector, params.timeout)
                msg = f'👀  Element with selector "{params.selector}" became visible within {params.timeout}ms.'
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                err_msg = f'❌  Failed to wait for element "{params.selector}" within {params.timeout}ms: {str(e)}'
                raise Exception(err_msg)

        @self.registry.action('Click element by index', param_model=ClickElementAction)
        async def click_element_by_index(params: ClickElementAction, browser: BrowserContext):
            session = await browser.get_session()

            if params.index not in await browser.get_selector_map():
                raise Exception(f'Element with index {params.index} does not exist - retry or use alternative actions')

            element_node = await browser.get_dom_element_by_index(params.index)
            initial_pages = len(session.context.pages)

            if await browser.is_file_uploader(element_node):
                msg = f'Index {params.index} - has an element which opens file upload dialog. To upload files please use a specific function to upload files '
                return ActionResult(extracted_content=msg, include_in_memory=True)

            try:
                download_path = await browser._click_element_node(element_node)
                if download_path:
                    msg = f'💾  Downloaded file to {download_path}'
                else:
                    msg = f'🖱️  Clicked button with index {params.index}: {element_node.get_all_text_till_next_clickable_element(max_depth=2)}'

                if len(session.context.pages) > initial_pages:
                    new_tab_msg = 'New tab opened - switching to it'
                    msg += f' - {new_tab_msg}'
                    await browser.switch_to_tab(-1)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                return ActionResult(error=str(e))

        @self.registry.action('Click element by selector', param_model=ClickElementBySelectorAction)
        async def click_element_by_selector(params: ClickElementBySelectorAction, browser: BrowserContext):
            try:
                element_node = await browser.get_locate_element_by_css_selector(params.css_selector)
                if element_node:
                    try:
                        await element_node.scroll_into_view_if_needed()
                        await element_node.click(timeout=500, force=True)
                    except Exception:
                        try:
                            await element_node.evaluate('el => el.click()')
                        except Exception as e:
                            return ActionResult(error=str(e))
                    return ActionResult(
                        extracted_content=f'🖱️  Clicked on element with text "{params.css_selector}"',
                        include_in_memory=True
                    )
            except Exception as e:
                return ActionResult(error=str(e))

        @self.registry.action('Click on element by xpath', param_model=ClickElementByXpathAction)
        async def click_element_by_xpath(params: ClickElementByXpathAction, browser: BrowserContext):
            try:
                element_node = await browser.get_locate_element_by_xpath(params.xpath)
                if element_node:
                    try:
                        await element_node.scroll_into_view_if_needed()
                        await element_node.click(timeout=500, force=True)
                    except Exception:
                        try:
                            await element_node.evaluate('el => el.click()')
                        except Exception as e:
                            return ActionResult(error=str(e))
                    msg = f'🖱️  Clicked on element with text "{params.xpath}"'
                    return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                return ActionResult(error=str(e))

        @self.registry.action('Click element with text', param_model=ClickElementByTextAction)
        async def click_element_by_text(params: ClickElementByTextAction, browser: BrowserContext):
            try:
                element_node = await browser.get_locate_element_by_text(text=params.text, nth=params.nth, element_type=params.element_type)

                if element_node:
                    try:
                        await element_node.scroll_into_view_if_needed()
                        await element_node.click(timeout=500, force=True)
                    except Exception:
                        try:
                            await element_node.evaluate('el => el.click()')
                        except Exception as e:
                            return ActionResult(error=str(e))
                    msg = f'🖱️  Clicked on element with text "{params.text}"'
                    return ActionResult(extracted_content=msg, include_in_memory=True)
                else:
                    return ActionResult(error=f"No element found for text '{params.text}'")
            except Exception as e:
                return ActionResult(error=str(e))

        @self.registry.action('Input text into a input interactive element', param_model=InputTextAction)
        async def input_text(params: InputTextAction, browser: BrowserContext, has_sensitive_data: bool = False):
            if params.index not in await browser.get_selector_map():
                raise Exception(f'Element index {params.index} does not exist - retry or use alternative actions')

            element_node = await browser.get_dom_element_by_index(params.index)
            await browser._input_text_element_node(element_node, params.text)
            if not has_sensitive_data:
                msg = f'⌨️  Input {params.text} into index {params.index}'
            else:
                msg = f'⌨️  Input sensitive data into index {params.index}'
            return ActionResult(extracted_content=msg, include_in_memory=True)

        @self.registry.action('Switch tab', param_model=SwitchTabAction)
        async def switch_tab(params: SwitchTabAction, browser: BrowserContext):
            await browser.switch_to_tab(params.page_id)
            page = await browser.get_current_page()
            await page.wait_for_load_state()
            msg = f'🔄  Switched to tab {params.page_id}'
            return ActionResult(extracted_content=msg, include_in_memory=True)

        @self.registry.action('Open url in new tab', param_model=OpenTabAction)
        async def open_tab(params: OpenTabAction, browser: BrowserContext):
            await browser.create_new_tab(params.url)
            msg = f'🔗  Opened new tab with {params.url}'
            return ActionResult(extracted_content=msg, include_in_memory=True)

        @self.registry.action('Close an existing tab', param_model=CloseTabAction)
        async def close_tab(params: CloseTabAction, browser: BrowserContext):
            await browser.switch_to_tab(params.page_id)
            page = await browser.get_current_page()
            url = page.url
            await page.close()
            msg = f'❌  Closed tab #{params.page_id} with url {url}'
            return ActionResult(extracted_content=msg, include_in_memory=True)

        @self.registry.action(
            'Scroll down the page by pixel amount - if no amount is specified, scroll down one page',
            param_model=ScrollAction,
        )
        async def scroll_down(params: ScrollAction, browser: BrowserContext):
            page = await browser.get_current_page()
            if params.amount is not None:
                await page.evaluate(f'window.scrollBy(0, {params.amount});')
            else:
                await page.evaluate('window.scrollBy(0, window.innerHeight);')

            amount = f'{params.amount} pixels' if params.amount is not None else 'one page'
            msg = f'🔍  Scrolled down the page by {amount}'
            return ActionResult(
                extracted_content=msg,
                include_in_memory=True,
            )

        @self.registry.action(
            'Scroll up the page by pixel amount - if no amount is specified, scroll up one page',
            param_model=ScrollAction,
        )
        async def scroll_up(params: ScrollAction, browser: BrowserContext):
            page = await browser.get_current_page()
            if params.amount is not None:
                await page.evaluate(f'window.scrollBy(0, -{params.amount});')
            else:
                await page.evaluate('window.scrollBy(0, -window.innerHeight);')

            amount = f'{params.amount} pixels' if params.amount is not None else 'one page'
            msg = f'🔍  Scrolled up the page by {amount}'
            return ActionResult(
                extracted_content=msg,
                include_in_memory=True,
            )

        @self.registry.action(
            'Send strings of special keys like Escape,Backspace, Insert, PageDown, Delete, Enter, Shortcuts such as `Control+o`, `Control+Shift+T` are supported as well. This gets used in keyboard.press. ',
            param_model=SendKeysAction
        )
        async def send_keys(params: SendKeysAction, browser: BrowserContext):
            page = await browser.get_current_page()

            try:
                await page.keyboard.press(params.keys)
            except Exception as e:
                if 'Unknown key' in str(e):
                    for key in params.keys:
                        try:
                            await page.keyboard.press(key)
                        except Exception as e:
                            raise e
                else:
                    raise e
            msg = f'⌨️  Sent keys: {params.keys}'
            return ActionResult(extracted_content=msg, include_in_memory=True)

        @self.registry.action(description='If you dont find something which you want to interact with, scroll to it')
        async def scroll_to_text(text: str, browser: BrowserContext):
            page = await browser.get_current_page()
            try:
                locators = [page.get_by_text(text, exact=False), page.locator(f'text={text}'), page.locator(f"//*[contains(text(), '{text}')]")]

                for locator in locators:
                    try:
                        if await locator.count() > 0 and await locator.first.is_visible():
                            await locator.first.scroll_into_view_if_needed()
                            await asyncio.sleep(0.5)
                            msg = f'🔍  Scrolled to text: {text}'
                            return ActionResult(extracted_content=msg, include_in_memory=True)
                    except Exception as e:
                        continue

                msg = f"Text '{text}' not found or not visible on page"
                return ActionResult(extracted_content=msg, include_in_memory=True)

            except Exception as e:
                msg = f"Failed to scroll to text '{text}': {str(e)}"
                return ActionResult(error=msg, include_in_memory=True)

        @self.registry.action(description='Get all options from a native dropdown')
        async def get_dropdown_options(index: int, browser: BrowserContext) -> ActionResult:
            page = await browser.get_current_page()
            selector_map = await browser.get_selector_map()
            dom_element = selector_map[index]

            try:
                all_options = []
                frame_index = 0

                for frame in page.frames:
                    options = await frame.evaluate(
                        """
                            (xpath) => {
                                const select = document.evaluate(xpath, document, null,
                                    XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                                if (!select) return null;
    
                                return {
                                    options: Array.from(select.options).map(opt => ({
                                        text: opt.text, //do not trim, because we are doing exact match in select_dropdown_option
                                        value: opt.value,
                                        index: opt.index
                                    })),
                                    id: select.id,
                                    name: select.name
                                };
                            }
                        """,
                        dom_element.xpath,
                    )

                    if options:
                        formatted_options = []
                        for opt in options['options']:
                            encoded_text = json.dumps(opt['text'])
                            formatted_options.append(f'{opt["index"]}: text={encoded_text}')

                        all_options.extend(formatted_options)

                    frame_index += 1

                if all_options:
                    msg = '\n'.join(all_options)
                    msg += '\nUse the exact text string in select_dropdown_option'
                    return ActionResult(extracted_content=msg, include_in_memory=True)
                else:
                    msg = 'No options found in any frame for dropdown'
                    return ActionResult(extracted_content=msg, include_in_memory=True)

            except Exception as e:
                msg = f'Error getting options: {str(e)}'
                return ActionResult(extracted_content=msg, include_in_memory=True)

        @self.registry.action(description='Select dropdown option for interactive element index by the text of the option you want to select')
        async def select_dropdown_option(index: int, text: str, browser: BrowserContext) -> ActionResult:
            page = await browser.get_current_page()
            selector_map = await browser.get_selector_map()
            dom_element = selector_map[index]

            if dom_element.tag_name != 'select':
                msg = f'Cannot select option: Element with index {index} is a {dom_element.tag_name}, not a select'
                return ActionResult(extracted_content=msg, include_in_memory=True)

            xpath = '//' + dom_element.xpath

            try:
                frame_index = 0
                for frame in page.frames:
                    find_dropdown_js = """
                        (xpath) => {
                            try {
                                const select = document.evaluate(xpath, document, null,
                                    XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                                if (!select) return null;
                                if (select.tagName.toLowerCase() !== 'select') {
                                    return {
                                        error: `Found element but it's a ${select.tagName}, not a SELECT`,
                                        found: false
                                    };
                                }
                                return {
                                    id: select.id,
                                    name: select.name,
                                    found: true,
                                    tagName: select.tagName,
                                    optionCount: select.options.length,
                                    currentValue: select.value,
                                    availableOptions: Array.from(select.options).map(o => o.text.trim())
                                };
                            } catch (e) {
                                return {error: e.toString(), found: false};
                            }
                        }
                    """

                    dropdown_info = await frame.evaluate(find_dropdown_js, dom_element.xpath)

                    if dropdown_info:
                        if not dropdown_info.get('found'): continue

                        selected_option_values = await frame.locator('//' + dom_element.xpath).nth(0).select_option(label=text, timeout=1000)

                        msg = f'selected option {text} with value {selected_option_values}'
                        return ActionResult(extracted_content=msg, include_in_memory=True)

                    frame_index += 1

                msg = f"Could not select option '{text}' in any frame"
                return ActionResult(extracted_content=msg, include_in_memory=True)

            except Exception as e:
                msg = f'Selection failed: {str(e)}'
                return ActionResult(error=msg, include_in_memory=True)

        @self.registry.action(
            'Drag and drop elements or between coordinates on the page - useful for canvas drawing, sortable lists, sliders, file uploads, and UI rearrangement',
            param_model=DragDropAction
        )
        async def drag_drop(params: DragDropAction, browser: BrowserContext) -> ActionResult:
            async def get_drag_elements(page: Page, source_selector: str, target_selector: str) -> Tuple[Optional[ElementHandle], Optional[ElementHandle]]:
                source_element = None
                target_element = None

                source_locator = page.locator(source_selector)
                target_locator = page.locator(target_selector)

                source_count = await source_locator.count()
                target_count = await target_locator.count()

                if source_count > 0: source_element = await source_locator.first.element_handle()
                if target_count > 0: target_element = await target_locator.first.element_handle()

                return source_element, target_element

            async def get_element_coordinates(source_element: ElementHandle, target_element: ElementHandle, source_position: Optional[Position], target_position: Optional[Position]) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
                source_coords, target_coords = None, None

                if source_position:
                    source_coords = (source_position.x, source_position.y)
                else:
                    source_box = await source_element.bounding_box()
                    if source_box:
                        source_coords = (int(source_box['x'] + source_box['width'] / 2), int(source_box['y'] + source_box['height'] / 2))

                if target_position: target_coords = (target_position.x, target_position.y)
                else:
                    target_box = await target_element.bounding_box()
                    if target_box:
                        target_coords = (int(target_box['x'] + target_box['width'] / 2), int(target_box['y'] + target_box['height'] / 2))

                return source_coords, target_coords

            async def execute_drag_operation(page: Page, source_x: int, source_y: int, target_x: int, target_y: int, steps: int, delay_ms: int) -> Tuple[bool, str]:
                try:
                    try:
                        await page.mouse.move(source_x, source_y)
                    except Exception as e:
                        return False, f'Failed to move to source position: {str(e)}'

                    await page.mouse.down()

                    for i in range(1, steps + 1):
                        ratio = i / steps
                        intermediate_x = int(source_x + (target_x - source_x) * ratio)
                        intermediate_y = int(source_y + (target_y - source_y) * ratio)

                        await page.mouse.move(intermediate_x, intermediate_y)
                        if delay_ms > 0: await asyncio.sleep(delay_ms / 1000)

                    await page.mouse.move(target_x, target_y)
                    await page.mouse.move(target_x, target_y)
                    await page.mouse.up()

                    return True, 'Drag operation completed successfully'

                except Exception as e:
                    return False, f'Error during drag operation: {str(e)}'

            page = await browser.get_current_page()

            try:
                source_x: Optional[int] = None
                source_y: Optional[int] = None
                target_x: Optional[int] = None
                target_y: Optional[int] = None

                steps = max(1, params.steps or 10)
                delay_ms = max(0, params.delay_ms or 5)

                if params.element_source and params.element_target:
                    source_element, target_element = await get_drag_elements(page, params.element_source, params.element_target)

                    if not source_element or not target_element:
                        error_msg = f'Failed to find {"source" if not source_element else "target"} element'
                        return ActionResult(error=error_msg, include_in_memory=True)

                    source_coords, target_coords = await get_element_coordinates(
                        source_element, target_element, params.element_source_offset, params.element_target_offset
                    )

                    if not source_coords or not target_coords:
                        error_msg = f'Failed to determine {"source" if not source_coords else "target"} coordinates'
                        return ActionResult(error=error_msg, include_in_memory=True)

                    source_x, source_y = source_coords
                    target_x, target_y = target_coords

                elif all(
                        coord is not None for coord in
                        [params.coord_source_x, params.coord_source_y, params.coord_target_x, params.coord_target_y]
                ):
                    source_x = params.coord_source_x
                    source_y = params.coord_source_y
                    target_x = params.coord_target_x
                    target_y = params.coord_target_y
                else:
                    error_msg = 'Must provide either source/target selectors or source/target coordinates'
                    return ActionResult(error=error_msg, include_in_memory=True)

                if any(coord is None for coord in [source_x, source_y, target_x, target_y]):
                    error_msg = 'Failed to determine source or target coordinates'
                    return ActionResult(error=error_msg, include_in_memory=True)

                success, message = await execute_drag_operation(
                    page, cast(int, source_x), cast(int, source_y), cast(int, target_x), cast(int, target_y),
                    steps, delay_ms
                )

                if not success:
                    return ActionResult(error=message, include_in_memory=True)

                if params.element_source and params.element_target:
                    msg = f"🖱️ Dragged element '{params.element_source}' to '{params.element_target}'"
                else:
                    msg = f'🖱️ Dragged from ({source_x}, {source_y}) to ({target_x}, {target_y})'

                return ActionResult(extracted_content=msg, include_in_memory=True)

            except Exception as e:
                error_msg = f'Failed to perform drag and drop: {str(e)}'
                return ActionResult(error=error_msg, include_in_memory=True)

    def action(self, description: str, **kwargs):
        return self.registry.action(description, **kwargs)

    async def act(self, action: dict, browser_context: BrowserContext) -> ActionResult:
        try:
            for action_name, params in action.items():
                if params is not None:
                    result = await self.registry.execute_action(action_name, params, browser=browser_context)

                    if isinstance(result, str):
                        return ActionResult(extracted_content=result)
                    elif isinstance(result, ActionResult):
                        return result
                    elif result is None:
                        return ActionResult()
                    else:
                        raise ValueError(f'Invalid action result type: {type(result)} of {result}')
            return ActionResult()
        except Exception as e:
            raise e
