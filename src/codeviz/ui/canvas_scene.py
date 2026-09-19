"""UI-neutral graph scene and viewport geometry."""

from dataclasses import dataclass

from codeviz.core import EdgeType, VisibleGraph

from . import theme

MIN_ZOOM = 0.25
MAX_ZOOM = 2.5


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    def intersects(self, other: "Rect", margin: float = 0) -> bool:
        """Return whether the rectangles overlap after applying a margin."""
        return not (
            self.x + self.width + margin < other.x
            or other.x + other.width + margin < self.x
            or self.y + self.height + margin < other.y
            or other.y + other.height + margin < self.y
        )


@dataclass(frozen=True, slots=True)
class Viewport:
    zoom: float = 1.0
    pan: Point = Point(0.0, 0.0)


def scene_to_screen(point: Point, viewport: Viewport) -> Point:
    return Point(point.x * viewport.zoom + viewport.pan.x,
                 point.y * viewport.zoom + viewport.pan.y)


def screen_to_scene(point: Point, viewport: Viewport) -> Point:
    return Point((point.x - viewport.pan.x) / viewport.zoom,
                 (point.y - viewport.pan.y) / viewport.zoom)


def zoom_about(viewport: Viewport, screen_anchor: Point, requested_zoom: float) -> Viewport:
    scene_anchor = screen_to_scene(screen_anchor, viewport)
    zoom = min(MAX_ZOOM, max(MIN_ZOOM, requested_zoom))
    return Viewport(zoom, Point(screen_anchor.x - scene_anchor.x * zoom,
                                screen_anchor.y - scene_anchor.y * zoom))


@dataclass(frozen=True, slots=True)
class SceneNode:
    id: str
    label: str
    subtitle: str
    x: float
    y: float
    width: float
    height: float
    selected: bool
    neighbor: bool
    member_count: int


@dataclass(frozen=True, slots=True)
class SceneEdge:
    source: str
    target: str
    type: EdgeType
    style: str
    points: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class GraphScene:
    nodes: tuple[SceneNode, ...]
    edges: tuple[SceneEdge, ...]
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class EdgePaint:
    color: str
    dash: tuple[int, int] | None
    width: int


def edge_paint(style: str) -> EdgePaint:
    return {
        "structural-dotted": EdgePaint(theme.MUTED, (3, 4), 1),
        "control-solid": EdgePaint(theme.ACCENT, None, 2),
        "branch-true": EdgePaint("#8acb99", (6, 3), 2),
        "branch-false": EdgePaint(theme.WARNING, (6, 3), 2),
        "data-solid": EdgePaint("#7fb3e8", None, 3),
        "loop-curved": EdgePaint(theme.WARNING, (4, 3), 2),
        "calls-dashed": EdgePaint(theme.ACCENT, (6, 3), 2),
        "data-dashed": EdgePaint("#7fb3e8", (5, 3), 2),
    }.get(style, EdgePaint(theme.MUTED, None, 1))


def graph_scene(view: VisibleGraph, selected_id: str | None = None,
                *, viewport_width: float = 0.0) -> GraphScene:
    """Calculate drawing instructions without creating a Tk window."""
    neighbors = {edge.target if edge.source == selected_id else edge.source
                 for edge in view.edges if selected_id in (edge.source, edge.target)}
    positions = view.layout_hints
    min_x = min((point[0] for point in positions.values()), default=0.0)
    min_y = min((point[1] for point in positions.values()), default=0.0)
    max_x = max((point[0] + 170.0 for point in positions.values()), default=170.0)
    content_width = max_x - min_x
    shift_x = max(80.0 - min_x, (viewport_width - content_width) / 2 - min_x)
    shift_y = 80.0 - min(0.0, min_y)
    nodes = tuple(SceneNode(
        node.id, node.display_name, node.code_name or "",
        positions.get(node.id, (0.0, 0.0))[0] + shift_x,
        positions.get(node.id, (0.0, 0.0))[1] + shift_y,
        170.0, 66.0, node.id == selected_id, node.id in neighbors,
        len(node.member_ids),
    ) for node in view.nodes)
    by_id = {node.id: node for node in nodes}
    styles = {item.type: item.style for item in view.legend}
    edges: list[SceneEdge] = []
    for edge in view.edges:
        source, target = by_id[edge.source], by_id[edge.target]
        points: tuple[float, ...]
        if edge.type == EdgeType.LOOP_BACK:
            points = (source.x + source.width, source.y + source.height / 2,
                      source.x + source.width + 35, source.y + source.height / 2,
                      source.x + source.width + 35, target.y - 22,
                      target.x + target.width / 2, target.y - 22,
                      target.x + target.width / 2, target.y)
        else:
            points = (source.x + source.width / 2, source.y + source.height,
                      target.x + target.width / 2, target.y)
        edges.append(SceneEdge(edge.source, edge.target, edge.type,
                               styles.get(edge.type, edge.type.value), points))
    width = max(max((node.x + node.width for node in nodes), default=0.0) + 80.0,
                viewport_width)
    height = max((node.y + node.height for node in nodes), default=0.0) + 80.0
    return GraphScene(nodes, tuple(edges), width, height)
