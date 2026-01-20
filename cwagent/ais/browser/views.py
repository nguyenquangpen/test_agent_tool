# from typing import Any, Optional

from pydantic import BaseModel

from cwagent.ais.dom.models import DOMElementNode, SelectorMap


class BrowserError(Exception):
    """Custom exception for browser-related errors"""

    pass


class URLNotAllowedError(BrowserError):
    """Exception raised when attempting to navigate to a non-allowed URL"""

    pass


class TabInfo(BaseModel):
    """Information about a browser tab"""

    page_id: int
    url: str
    title: str


class BrowserState(BaseModel):
    """Current state of the browser context"""

    element_tree: DOMElementNode
    selector_map: SelectorMap
    url: str
    title: str
    tabs: list[TabInfo]
    screenshot: str
    pixels_above: int
    pixels_below: int

    class Config:
        arbitrary_types_allowed = True
