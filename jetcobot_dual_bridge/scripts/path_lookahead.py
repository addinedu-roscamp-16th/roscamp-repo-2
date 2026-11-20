"""Joint-space progress and an ahead target on a MoveIt path (radians)."""
import math


def path_geometry(points):
    positions = [tuple(q) for _, q in points]
    if len(positions) < 2:
        raise ValueError('Lookahead needs at least two trajectory points')
    lengths = [math.dist(a, b) for a, b in zip(positions, positions[1:])]
    cumulative = [0.0]
    for length in lengths:
        cumulative.append(cumulative[-1] + length)
    if cumulative[-1] <= 1e-9:
        raise ValueError('Lookahead path has no movement')
    return positions, cumulative


def position_at(positions, cumulative, distance):
    distance = max(0.0, min(cumulative[-1], distance))
    for index in range(len(positions)-1):
        if distance <= cumulative[index+1] or index == len(positions)-2:
            length = cumulative[index+1]-cumulative[index]
            fraction = (distance-cumulative[index])/length if length > 1e-12 else 0.0
            return tuple(a+fraction*(b-a) for a, b in zip(positions[index], positions[index+1]))
    return positions[-1]


def project_forward(positions, cumulative, actual, previous_distance=0.0,
                    search_ahead=math.radians(20.0)):
    """Find the nearest forward path point within a bounded progress window.

    The bound prevents a self-crossing path from jumping to a distant later
    branch. A small backward window accommodates measurement noise.
    """
    low = max(0.0, previous_distance-math.radians(2.0))
    high = min(cumulative[-1], previous_distance+search_ahead)
    best = None
    for index, (start, end) in enumerate(zip(positions, positions[1:])):
        span = cumulative[index+1]-cumulative[index]
        if cumulative[index+1] < low or cumulative[index] > high:
            continue
        delta = tuple(b-a for a, b in zip(start, end))
        norm2 = sum(value*value for value in delta)
        fraction = sum((v-a)*d for v, a, d in zip(actual, start, delta))/norm2 if norm2 > 1e-12 else 0.0
        fraction = max(0.0, min(1.0, fraction))
        candidate_distance = cumulative[index]+fraction*span
        candidate_distance = max(low, min(high, candidate_distance))
        candidate = position_at(positions, cumulative, candidate_distance)
        error = max(abs(v-q) for v, q in zip(actual, candidate))
        score = (math.dist(actual, candidate), -candidate_distance)
        if best is None or score < best[0]:
            best = (score, candidate_distance, error)
    if best is None:
        raise ValueError('No path segment in forward search window')
    return max(previous_distance, best[1]), best[2]
