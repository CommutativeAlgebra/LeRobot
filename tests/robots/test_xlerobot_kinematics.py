import pytest

from lerobot.model.SO101Robot import SO101Kinematics


@pytest.mark.parametrize(
    ("shoulder_lift", "elbow_flex"),
    [
        (0.0, 0.0),
        (-20.0, 20.0),
        (30.0, -10.0),
        (45.0, 45.0),
        (-45.0, 60.0),
    ],
)
def test_so101_planar_kinematics_round_trip(shoulder_lift: float, elbow_flex: float) -> None:
    kinematics = SO101Kinematics()

    x, y = kinematics.forward_kinematics(shoulder_lift, elbow_flex)
    recovered_shoulder, recovered_elbow = kinematics.inverse_kinematics(x, y)

    assert recovered_shoulder == pytest.approx(shoulder_lift)
    assert recovered_elbow == pytest.approx(elbow_flex)
