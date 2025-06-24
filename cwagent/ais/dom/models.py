from dataclasses import dataclass
from typing import Dict, List, Optional

from pydantic import BaseModel


class Coordinates(BaseModel):
    x: int
    y: int


class CoordinateSet(BaseModel):
    top_left: Coordinates
    top_right: Coordinates
    bottom_left: Coordinates
    bottom_right: Coordinates
    center: Coordinates
    width: int
    height: int


class ViewportInfo(BaseModel):
    scroll_x: int
    scroll_y: int
    width: int
    height: int


@dataclass(frozen=False)
class DOMBaseNode:
    is_visible: bool
    parent: Optional['DOMElementNode']


@dataclass(frozen=False)
class DOMTextNode(DOMBaseNode):
    text: str
    type: str = 'TEXT_NODE'


@dataclass(frozen=False)
class DOMElementNode(DOMBaseNode):
    tag_name: str
    xpath: str
    attributes: Dict[str, str]
    children: List[DOMBaseNode]
    is_interactive: bool = False
    is_top_element: bool = False
    is_in_viewport: bool = False
    shadow_root: bool = False
    highlight_index: Optional[int] = None
    viewport_coordinates: Optional[CoordinateSet] = None
    page_coordinates: Optional[CoordinateSet] = None
    viewport_info: Optional[ViewportInfo] = None

    def __repr__(self) -> str:
        tag_str = f'<{self.tag_name}'

        for key, value in self.attributes.items():
            tag_str += f' {key}="{value}"'
        tag_str += '>'

        extras = []
        if self.is_interactive:
            extras.append('interactive')
        if self.is_top_element:
            extras.append('top')
        if self.shadow_root:
            extras.append('shadow-root')
        if self.highlight_index is not None:
            extras.append(f'highlight:{self.highlight_index}')
        if self.is_in_viewport:
            extras.append('in-viewport')

        if extras:
            tag_str += f' [{", ".join(extras)}]'

        return tag_str

    def get_all_text_till_next_clickable_element(self, max_depth: int = -1) -> str:
        text_parts = []

        def collect_text(node: DOMBaseNode, current_depth: int) -> None:
            if max_depth != -1 and current_depth > max_depth: return
            if isinstance(node, DOMElementNode) and node != self and node.highlight_index is not None:
                return

            if isinstance(node, DOMTextNode):
                text_parts.append(node.text)
            elif isinstance(node, DOMElementNode):
                for child in node.children:
                    collect_text(child, current_depth + 1)

        collect_text(self, 0)
        return '\n'.join(text_parts).strip()

    def get_file_upload_element(self, check_siblings: bool = True) -> Optional['DOMElementNode']:
        if self.tag_name == 'input' and self.attributes.get('type') == 'file':
            return self

        for child in self.children:
            if isinstance(child, DOMElementNode):
                result = child.get_file_upload_element(check_siblings=False)
                if result:
                    return result

        if check_siblings and self.parent:
            for sibling in self.parent.children:
                if sibling is not self and isinstance(sibling, DOMElementNode):
                    result = sibling.get_file_upload_element(check_siblings=False)
                    if result:
                        return result

        return None


SelectorMap = dict[int, DOMElementNode]


@dataclass
class DOMState:
    element_tree: DOMElementNode
    selector_map: SelectorMap


@dataclass
class ViewportInfo:
    width: int
    height: int
