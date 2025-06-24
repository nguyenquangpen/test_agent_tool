import fnmatch

from urllib.parse import urlparse

from typing import Optional, Callable, Dict, Type
from pydantic import BaseModel, ConfigDict, Field, model_validator
from playwright.async_api import Page


class RegisteredAction(BaseModel):
    name: str
    description: str
    function: Callable
    param_model: Type[BaseModel]

    domains: list[str] | None = None
    page_filter: Callable[[Page], bool] | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def prompt_description(self) -> str:
        skip_keys = ['title']
        s = f'{self.description}: \n'
        s += '{' + str(self.name) + ': '
        s += str(
            {
                k: {sub_k: sub_v for sub_k, sub_v in v.items() if sub_k not in skip_keys}
                for k, v in self.param_model.model_json_schema()['properties'].items()
            }
        )
        s += '}'
        return s


class ActionModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_index(self) -> int | None:
        params = self.model_dump(exclude_unset=True).values()
        if not params:
            return None
        for param in params:
            if param is not None and 'index' in param:
                return param['index']

        return None

    def set_index(self, index: int):
        action_data = self.model_dump(exclude_unset=True)
        action_name = next(iter(action_data.keys()))
        action_params = getattr(self, action_name)

        if hasattr(action_params, 'index'):
            action_params.index = index


class ActionRegistry(BaseModel):
    actions: Dict[str, RegisteredAction] = {}

    @staticmethod
    def _match_domains(domains: list[str] | None, url: str) -> bool:
        if domains is None or not url: return True

        try:
            parsed_url = urlparse(url)
            if not parsed_url.netloc:
                return False

            domain = parsed_url.netloc
            if ':' in domain:
                domain = domain.split(':')[0]

            for domain_pattern in domains:
                if fnmatch.fnmatch(domain, domain_pattern): return True
            return False
        except Exception:
            return False

    @staticmethod
    def _match_page_filter(page_filter: Callable[[Page], bool] | None, page: Page) -> bool:
        """Match a page filter against a page"""
        if page_filter is None:
            return True
        return page_filter(page)

    def get_prompt_description(self, page: Page | None = None) -> str:
        if page is None:
            return '\n'.join(
                action.prompt_description()
                for action in self.actions.values()
                if action.page_filter is None and action.domains is None
            )

        filtered_actions = []
        for action in self.actions.values():
            if not (action.domains or action.page_filter): continue

            domain_is_allowed = self._match_domains(action.domains, page.url)
            page_is_allowed = self._match_page_filter(action.page_filter, page)

            if domain_is_allowed and page_is_allowed: filtered_actions.append(action)

        return '\n'.join(action.prompt_description() for action in filtered_actions)


class ControllerActionModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_index(self) -> int | None:
        params = self.model_dump(exclude_unset=True).values()
        if not params:
            return None
        for param in params:
            if param is not None and 'index' in param:
                return param['index']
        return None

    def set_index(self, index: int):
        action_data = self.model_dump(exclude_unset=True)
        action_name = next(iter(action_data.keys()))
        action_params = getattr(self, action_name)

        if hasattr(action_params, 'index'):
            action_params.index = index


class GoToUrlAction(BaseModel):
    url: str


class WaitForElementAction(BaseModel):
    selector: str
    timeout: Optional[int] = 10000


class ClickElementAction(BaseModel):
    index: int
    xpath: Optional[str] = None


class ClickElementByXpathAction(BaseModel):
    xpath: str


class ClickElementBySelectorAction(BaseModel):
    css_selector: str


class ClickElementByTextAction(BaseModel):
    text: str
    element_type: Optional[str]
    nth: int = 0


class InputTextAction(BaseModel):
    index: int
    text: str
    xpath: Optional[str] = None


class DoneAction(BaseModel):
    text: str
    success: bool


class SwitchTabAction(BaseModel):
    page_id: int


class OpenTabAction(BaseModel):
    url: str


class CloseTabAction(BaseModel):
    page_id: int


class ScrollAction(BaseModel):
    amount: Optional[int] = None


class SendKeysAction(BaseModel):
    keys: str


class NoParamsAction(BaseModel):
    model_config = ConfigDict(extra='allow')

    @model_validator(mode='before')
    def ignore_all_inputs(cls, values):
        return {}


class Position(BaseModel):
    x: int
    y: int


class DragDropAction(BaseModel):
    element_source: Optional[str] = Field(None, description='CSS selector or XPath of the element to drag from')
    element_target: Optional[str] = Field(None, description='CSS selector or XPath of the element to drop onto')
    element_source_offset: Optional[Position] = Field(
        None, description='Precise position within the source element to start drag (in pixels from top-left corner)'
    )
    element_target_offset: Optional[Position] = Field(
        None, description='Precise position within the target element to drop (in pixels from top-left corner)'
    )

    coord_source_x: Optional[int] = Field(None, description='Absolute X coordinate on page to start drag from (in pixels)')
    coord_source_y: Optional[int] = Field(None, description='Absolute Y coordinate on page to start drag from (in pixels)')
    coord_target_x: Optional[int] = Field(None, description='Absolute X coordinate on page to drop at (in pixels)')
    coord_target_y: Optional[int] = Field(None, description='Absolute Y coordinate on page to drop at (in pixels)')

    steps: Optional[int] = Field(10, description='Number of intermediate points for smoother movement (5-20 recommended)')
    delay_ms: Optional[int] = Field(5, description='Delay in milliseconds between steps (0 for fastest, 10-20 for more natural)')


class ActionResult(BaseModel):
    is_done: Optional[bool] = False
    success: Optional[bool] = None
    extracted_content: Optional[str] = None
    error: Optional[str] = None
    include_in_memory: bool = False

