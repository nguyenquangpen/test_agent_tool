import gc

from typing import Optional

from playwright.async_api import Page

from cwagent.ais.dom.models import DOMBaseNode, DOMElementNode, DOMState, DOMTextNode, SelectorMap, ViewportInfo
from utils import build_domtree_js


class DomService:
    def __init__(self, page: 'Page'):
        self.page = page
        self.xpath_cache = {}
        self.js_code = build_domtree_js

    async def get_clickable_elements(self, highlight_elements: bool = True, focus_element: int = -1,
            viewport_expansion: int = 0,
    ) -> DOMState:
        element_tree, selector_map = await self._build_dom_tree(highlight_elements, focus_element, viewport_expansion)
        return DOMState(element_tree=element_tree, selector_map=selector_map)

    async def _build_dom_tree(self, highlight_elements: bool, focus_element: int, viewport_expansion: int) -> tuple[DOMElementNode, SelectorMap]:
        if await self.page.evaluate('1+1') != 2:
            raise ValueError('The page cannot evaluate javascript code properly')

        if self.page.url == 'about:blank':
            return (
                DOMElementNode(tag_name='body', xpath='', attributes={}, children=[], is_visible=False, parent=None),
                {},
            )

        args = {
            'doHighlightElements': highlight_elements,
            'focusHighlightIndex': focus_element,
            'viewportExpansion': viewport_expansion
        }

        eval_page: dict = await self.page.evaluate(self.js_code, args)
        return await self._construct_dom_tree(eval_page)

    async def _construct_dom_tree(self, eval_page: dict) -> tuple[DOMElementNode, SelectorMap]:
        js_node_map = eval_page['map']
        js_root_id = eval_page['rootId']

        selector_map = {}
        node_map = {}

        for id, node_data in js_node_map.items():
            node, children_ids = self._parse_node(node_data)
            if node is None: continue

            node_map[id] = node

            if isinstance(node, DOMElementNode) and node.highlight_index is not None:
                selector_map[node.highlight_index] = node

            if isinstance(node, DOMElementNode):
                for child_id in children_ids:
                    if child_id not in node_map: continue

                    child_node = node_map[child_id]

                    child_node.parent = node
                    node.children.append(child_node)

        html_to_dict = node_map[str(js_root_id)]

        del node_map
        del js_node_map
        del js_root_id

        gc.collect()

        if html_to_dict is None or not isinstance(html_to_dict, DOMElementNode):
            raise ValueError('Failed to parse HTML to dictionary')

        return html_to_dict, selector_map

    def _parse_node(self, node_data: dict) -> tuple[Optional[DOMBaseNode], list[int]]:
        if not node_data: return None, []

        if node_data.get('type') == 'TEXT_NODE':
            text_node = DOMTextNode(
                text=node_data['text'],
                is_visible=node_data['isVisible'],
                parent=None,
            )
            return text_node, []

        viewport_info = None

        if 'viewport' in node_data:
            viewport_info = ViewportInfo(
                width=node_data['viewport']['width'],
                height=node_data['viewport']['height'],
            )

        element_node = DOMElementNode(
            tag_name=node_data['tagName'],
            xpath=node_data['xpath'],
            attributes=node_data.get('attributes', {}),
            children=[],
            is_visible=node_data.get('isVisible', False),
            is_interactive=node_data.get('isInteractive', False),
            is_top_element=node_data.get('isTopElement', False),
            is_in_viewport=node_data.get('isInViewport', False),
            highlight_index=node_data.get('highlightIndex'),
            shadow_root=node_data.get('shadowRoot', False),
            parent=None,
            viewport_info=viewport_info,
        )

        children_ids = node_data.get('children', [])
        return element_node, children_ids
