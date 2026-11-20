"""Pure validation for initial small-motion hardware trials (radians/seconds)."""
import math

ARM = tuple(f'{i}_Joint' for i in range(1, 7))
LIMITS_DEG = ((-168, 168), (-135, 135), (-150, 150),
              (-145, 145), (-165, 165), (-180, 180))


def validate(names, points, current, max_move_deg=5.0, arm_names=ARM,
             start_tolerance_rad=0.02):
    if not math.isfinite(max_move_deg) or not 0 < max_move_deg <= 180:
        raise ValueError('Movement envelope must be in (0,180] degrees')
    if not math.isfinite(start_tolerance_rad) or not 0 < start_tolerance_rad <= math.radians(5):
        raise ValueError('Start tolerance must be in (0,5] degrees')
    if len(names) != 6 or set(names) != set(arm_names):
        raise ValueError('Exactly the six arm joint names are required')
    if not 1 <= len(points) <= 2000:
        raise ValueError('Trajectory must contain 1..2000 points')
    if len(current) != 6 or not all(math.isfinite(x) for x in current):
        raise ValueError('Invalid current arm feedback')
    order = [names.index(name) for name in arm_names]
    result = []
    previous_t, previous_q = 0.0, tuple(current)
    for index, (stamp, values) in enumerate(points):
        if not math.isfinite(stamp) or not 0 <= stamp <= 120:
            raise ValueError('Trajectory duration must be finite and <=120 seconds')
        if len(values) != 6:
            raise ValueError('Each point must contain six positions')
        q = tuple(float(values[i]) for i in order)
        if not all(math.isfinite(x) for x in q):
            raise ValueError('Non-finite trajectory position')
        for i, value in enumerate(q):
            low, high = LIMITS_DEG[i]
            if not math.radians(low) <= value <= math.radians(high):
                raise ValueError(f'{arm_names[i]} exceeds hardware angle range')
            if abs(value - current[i]) > math.radians(max_move_deg):
                raise ValueError(f'{arm_names[i]} exceeds movement limit of {max_move_deg:g} degrees')
        if index == 0 and max(abs(a-b) for a,b in zip(q,current)) > start_tolerance_rad:
            raise ValueError(f'Trajectory start differs from actual position by '
                             f'>{math.degrees(start_tolerance_rad):g}deg; replan')
        if index > 0 and stamp <= previous_t:
            raise ValueError('Trajectory times must be strictly increasing')
        if stamp > previous_t:
            if max(abs(a-b)/(stamp-previous_t) for a,b in zip(q,previous_q)) > 0.08:
                raise ValueError('Trajectory segment exceeds trial velocity limit 0.08rad/s')
        previous_t, previous_q = stamp, q
        result.append((stamp,q))
    if result[-1][0] <= 0:
        raise ValueError('Trajectory needs a positive duration')
    return result


def interpolate(points, elapsed):
    if elapsed <= points[0][0]:
        return points[0][1]
    for (t0,q0),(t1,q1) in zip(points,points[1:]):
        if elapsed <= t1:
            f=(elapsed-t0)/(t1-t0)
            return tuple(a+f*(b-a) for a,b in zip(q0,q1))
    return points[-1][1]


class CommandGate:
    """Limit refresh commands; suppress tiny/duplicate targets and final repeats."""
    def __init__(self, current, interval=0.25, deadband=math.radians(0.5)):
        self.last = tuple(current)
        self.last_time = -math.inf
        self.interval = interval
        self.deadband = deadband
        self.final_handled = False

    def select(self, target, elapsed, final=False):
        if self.final_handled:
            return False
        difference = max(abs(a-b) for a,b in zip(target,self.last))
        if final and difference < 1e-6:
            self.final_handled = True
            return False
        if elapsed-self.last_time < self.interval:
            return False
        if not final and difference < self.deadband:
            return False
        self.last = tuple(target)
        self.last_time = elapsed
        self.final_handled = final
        return True


def sdk_goal_target(points, line_tolerance=math.radians(0.25)):
    """Reject preview detours that an endpoint SDK command cannot reproduce.

    Passing this test does NOT guarantee actual multi-axis path or timing.
    """
    start, target = points[0][1], points[-1][1]
    delta = [b-a for a,b in zip(start,target)]
    norm2 = sum(d*d for d in delta)
    previous = -1e-6
    for _,q in points:
        fraction = sum((v-a)*d for v,a,d in zip(q,start,delta))/norm2 if norm2>1e-12 else 0.0
        if not -1e-6 <= fraction <= 1.000001 or fraction<previous-1e-6:
            raise ValueError('SDK goal mode rejects reversal/overshoot in preview path')
        if any(abs(v-(a+fraction*d))>line_tolerance for v,a,d in zip(q,start,delta)):
            raise ValueError('SDK goal mode cannot execute this preview detour; path tracking is required')
        previous=fraction
    return tuple(target)
