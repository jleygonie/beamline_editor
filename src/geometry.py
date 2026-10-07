"""Floor-plan geometry: position and direction of the reference orbit along s."""
from __future__ import annotations

import bisect
import math

ARC_STEP = math.radians(1.0)  # max direction change between two samples of an arc
TOL = 1e-12


def _walk(node: tuple, s: float) -> tuple[float, float, float]:
    """(x, y, direction) at s, going along the segment that starts at node."""
    s0, x, y, phi, k = node
    ds = s - s0
    if abs(k) < TOL:
        return x + ds * math.cos(phi), y + ds * math.sin(phi), phi
    p = phi + k * ds
    return x + (math.sin(p) - math.sin(phi)) / k, y - (math.cos(p) - math.cos(phi)) / k, p


class Geometry:
    """Reference orbit in the floor plane.

    Coordinates are in m with x to the right and y upwards; the direction is counter-clockwise from +x (rad).
    A positive bend angle turns the beam to the right (clockwise), as in MAD-X. Thick bends follow an arc,
    zero-length bends are kinks. Outside the bends the line is straight, also before s0 and after s1.
    """

    def __init__(self, bends: list[tuple[float, float, float]], s0: float, s1: float,
                 origin: tuple[float, float, float] = (0.0, 0.0, 0.0)):
        """bends: (s_start, s_end, angle) of every element with a non-zero angle."""
        x, y, phi = origin
        nodes = [(s0, x, y, phi, 0.0)]  # (s, x, y, direction, curvature) at the start of each segment
        for b0, b1, angle in sorted(bends):
            b0 = max(b0, nodes[-1][0])  # an overlapping bend starts where the previous one ends
            if b1 < b0 - 1e-9:
                continue
            x, y, phi = _walk(nodes[-1], b0)
            if b1 - b0 > 1e-9:
                nodes.append((b0, x, y, phi, -angle / (b1 - b0)))
                x, y, phi = _walk(nodes[-1], b1)
                nodes.append((b1, x, y, phi, 0.0))
            else:
                nodes.append((b0, x, y, phi - angle, 0.0))
        self.nodes = nodes
        self._s = [n[0] for n in nodes]
        self.s0, self.s1 = s0, max(s1, s0)
        self.poly = [(s, *self.at(s)[:2]) for s in self.samples(self.s0, self.s1)]

    def _node(self, s: float) -> tuple:
        return self.nodes[max(bisect.bisect_right(self._s, s) - 1, 0)]

    def at(self, s: float) -> tuple[float, float, float]:
        """(x, y, direction) of the orbit at s."""
        return _walk(self._node(s), s)

    def curvature(self, s: float) -> float:
        """Curvature at s (1/m, negative when bending right); the larger one at a segment boundary."""
        a, b = self._node(s)[4], self._node(s - 1e-9)[4]
        return a if abs(a) >= abs(b) else b

    @property
    def bent(self) -> bool:
        return len(self.nodes) > 1

    def samples(self, a: float, b: float) -> list[float]:
        """s values from a to b that follow the orbit: segment ends, and arcs split in small steps."""
        if b < a:
            a, b = b, a
        out = [a]
        for c in sorted({n[0] for n in self.nodes if a < n[0] < b} | {b}):
            prev = out[-1]
            n = max(1, math.ceil(abs(self._node(prev)[4]) * (c - prev) / ARC_STEP))
            out += [prev + (c - prev) * j / n for j in range(1, n + 1)]
        return out

    def bounds(self) -> tuple[float, float, float, float]:
        """(x_min, y_min, x_max, y_max) of the orbit between s0 and s1."""
        xs, ys = [p[1] for p in self.poly], [p[2] for p in self.poly]
        return min(xs), min(ys), max(xs), max(ys)

    def project(self, x: float, y: float) -> tuple[float, float]:
        """(s, d) of the orbit point nearest to (x, y); d is the signed distance, positive to the right."""
        poly = self.poly
        best = None
        last = len(poly) - 2
        for i in range(last + 1):
            (sa, xa, ya), (sb, xb, yb) = poly[i], poly[i + 1]
            dx, dy = xb - xa, yb - ya
            n2 = dx * dx + dy * dy
            if n2 < TOL * TOL:
                continue
            t = ((x - xa) * dx + (y - ya) * dy) / n2  # the end segments extend beyond the line
            t = min(max(t, 0.0 if i > 0 else -math.inf), 1.0 if i < last else math.inf)
            qx, qy = xa + t * dx, ya + t * dy
            dist2 = (x - qx) ** 2 + (y - qy) ** 2
            if best is None or dist2 < best[0]:
                n = math.sqrt(n2)
                d = ((x - qx) * dy - (y - qy) * dx) / n  # positive to the right of the direction
                best = (dist2, sa + t * (sb - sa), d)
        if best is None:  # zero-length line
            s, (px, py, phi) = self.s0, self.at(self.s0)
            return (s + (x - px) * math.cos(phi) + (y - py) * math.sin(phi),
                    (x - px) * math.sin(phi) - (y - py) * math.cos(phi))
        return best[1], best[2]
