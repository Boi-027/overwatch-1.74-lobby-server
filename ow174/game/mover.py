"""Moves a player's body from its commands, so other players can see it.

This first version walks on a flat floor at the spawn height: no collision, gravity, jumps or
per-hero speeds. The client's own mover (with the map's collision) is much more; its axes and units
are the same: forward (sin yaw, 0, cos yaw), right (-cos yaw, 0, sin yaw), throttles s8 / 127, one
command frame per tick.
"""

import math

from ow174.game.commands import CROUCH, Command

RUN_SPEED = 5.5  # m/s, most heroes
CROUCH_SPEED = 3.0
TURN = 2 * math.pi / 65536
PITCH_LIMIT = 16192  # 89 degrees in s16 angle units


class FlatMover:
    def __init__(self, position, yaw: int = 0) -> None:
        self.position = list(position)
        self.velocity = (0.0, 0.0, 0.0)
        self.yaw = int(yaw)
        self.pitch = 0
        self.crouched = False

    def step(self, command: Command, seconds: float) -> None:
        self.yaw = command.yaw
        self.pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, command.pitch))
        self.crouched = bool(command.buttons & CROUCH)
        angle = command.yaw * TURN
        forward = max(-1.0, min(1.0, command.forward / 127))
        right = max(-1.0, min(1.0, command.right / 127))
        x = forward * math.sin(angle) - right * math.cos(angle)
        z = forward * math.cos(angle) + right * math.sin(angle)
        length = math.hypot(x, z)
        speed = CROUCH_SPEED if self.crouched else RUN_SPEED
        if length > 1.0:
            x, z = x / length, z / length
        self.velocity = (x * speed, 0.0, z * speed)
        self.position[0] += self.velocity[0] * seconds
        self.position[2] += self.velocity[2] * seconds
