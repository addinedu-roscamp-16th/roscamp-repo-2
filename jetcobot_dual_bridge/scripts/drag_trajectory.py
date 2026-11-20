"""Plan-only conversion from MoveIt joint data to myCobot 280 drag samples.

The sign/centre are confirmed by stationary readings from both user arms on
2026-11-13. A single stationary pose does not validate commanded servo-speed
behavior or continuous path tracking.
"""
import math

ENCODER_CENTRE = 2048
ENCODER_PER_DEGREE = 4096 / 360
ENCODER_SIGNS = (-1, -1, 1, -1, -1, -1)
LIMITS_DEG = ((-168, 168), (-135, 135), (-150, 150),
              (-145, 145), (-165, 165), (-180, 180))


def angles_to_encoders(angles_rad):
    if len(angles_rad) != 6 or not all(math.isfinite(q) for q in angles_rad):
        raise ValueError('Expected six finite joint angles')
    result = []
    for index, (angle, sign) in enumerate(zip(angles_rad, ENCODER_SIGNS)):
        degree = math.degrees(angle)
        low, high = LIMITS_DEG[index]
        if not low <= degree <= high:
            raise ValueError(f'Joint {index+1} exceeds firmware angle limit')
        encoder = round(ENCODER_CENTRE + sign*ENCODER_PER_DEGREE*degree)
        if not 0 <= encoder <= 4096:
            raise ValueError(f'Joint {index+1} encoder outside 0..4096')
        result.append(encoder)
    return tuple(result)


def velocities_to_servo_speeds(velocities_rad_s):
    if len(velocities_rad_s) != 6 or not all(math.isfinite(v) for v in velocities_rad_s):
        raise ValueError('Expected six finite joint velocities')
    speeds = tuple(round(abs(math.degrees(v))*ENCODER_PER_DEGREE)
                   for v in velocities_rad_s)
    if any(speed > 6000 for speed in speeds):
        raise ValueError('Servo speed exceeds SDK accepted range')
    return speeds


def check_stationary_mapping(angles_deg, encoders, tolerance_steps=2):
    """Compare a read-only angle/encoder pair with the expected conversion."""
    if len(angles_deg) != 6 or len(encoders) != 6:
        raise ValueError('Expected six angles and six encoders')
    predicted = angles_to_encoders(tuple(math.radians(float(q)) for q in angles_deg))
    errors = tuple(int(observed)-expected for observed, expected in zip(encoders, predicted))
    if any(abs(error) > tolerance_steps for error in errors):
        raise ValueError(f'Angle/encoder mapping mismatch: {errors}')
    return errors


def trajectory_to_drag(points):
    """Convert (time_s, six radians, six rad/s or empty) to drag samples.

    Missing velocities use centred finite differences. This is an offline
    conversion only; the speed command's physical effect is not calibrated.
    """
    if len(points) < 2:
        raise ValueError('Trajectory needs at least two points')
    prepared = []
    for index, (stamp, angles, velocities) in enumerate(points):
        stamp = float(stamp)
        if not math.isfinite(stamp) or stamp < 0 or (index and stamp <= prepared[-1][0]):
            raise ValueError('Trajectory times must increase')
        angles = tuple(angles)
        encoders = angles_to_encoders(angles)
        velocities = tuple(velocities)
        if velocities and (len(velocities) != 6 or
                           not all(math.isfinite(v) for v in velocities)):
            raise ValueError('Expected six finite velocities or none')
        prepared.append((stamp, angles, velocities, encoders))
    converted = []
    for index, (stamp, angles, velocities, encoders) in enumerate(prepared):
        if not velocities:
            before = prepared[max(0, index-1)]
            after = prepared[min(len(prepared)-1, index+1)]
            span = after[0]-before[0]
            velocities = tuple((b-a)/span for a, b in zip(before[1], after[1]))
        converted.append((stamp, encoders, velocities_to_servo_speeds(velocities)))
    return converted


def segment_drag_command(previous, target, max_speed_value=60):
    """Encoder target and proportional servo-speed values for one path segment."""
    if len(previous) != 6 or len(target) != 6 or not 1 <= max_speed_value <= 6000:
        raise ValueError('Invalid drag segment')
    delta = [abs(a-b) for a, b in zip(target, previous)]
    if not all(math.isfinite(d) for d in delta) or max(delta) <= 1e-9:
        raise ValueError('Drag segment must move')
    largest = max(delta)
    speeds = tuple(max(1, round(max_speed_value*d/largest))
                   if d > math.radians(0.1) else 0 for d in delta)
    return angles_to_encoders(target), speeds


def smooth_drag_command(previous, target, following, progress, max_speed_value=60):
    """Make an encoder target with centered path-direction speeds and end ramps."""
    if len(previous) != 6 or len(target) != 6 or len(following) != 6:
        raise ValueError('Expected three six-joint points')
    if not 1 <= max_speed_value <= 6000 or not 0.0 <= progress <= 1.0:
        raise ValueError('Invalid speed or path progress')
    # Centered difference keeps the speed vector from jumping at each corner.
    tangent = [b-a for a, b in zip(previous, following)]
    largest = max(abs(v) for v in tangent)
    if largest <= 1e-9:
        tangent = [b-a for a, b in zip(previous, target)]
        largest = max(abs(v) for v in tangent)
    if largest <= 1e-9:
        raise ValueError('Drag path segment must move')
    # Smoothly ramp command magnitude at both ends without stopping at
    # intermediate points. Firmware speed units are empirical, so keep a floor.
    ramp = 0.55 + 0.45 * min(1.0, max(0.0, 2.0*min(progress, 1.0-progress)))
    peak = max(1, round(max_speed_value*ramp))
    speeds = tuple(max(1, round(peak*abs(v)/largest))
                   if abs(b-a) > math.radians(0.05) else 0
                   for a, b, v in zip(previous, target, tangent))
    return angles_to_encoders(target), speeds
