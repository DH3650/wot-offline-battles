"""Pure-Python codec for the 0.9.22 (#1513) TankmanDescr compact descriptor.

Layout reverse-engineered from the pinned client's own bytecode
(``scripts.pkg: scripts/common/items/tankmen.py``,
``TankmanDescr.__initFromCompactDescr`` / ``makeCompactDescr``)::

    offset  field
    0       header  = (nationID << 4) | ITEM_TYPES.tankman(8)
    1       vehicleTypeID          (index inside the nation vehicle list)
    2       roleID                 (indexes SKILL_NAMES: 1..5 are the roles)
    3       roleLevel              (0..100)
    4       numSkills
    5..     numSkills skill-index bytes (SKILL_NAMES indices, learning order)
    5+N     lastSkillLevel         (progress of the LAST skill only; earlier
                                    skills are implicitly at MAX_SKILL_LEVEL)
    6+N     flags                  (bit0 isFemale, bit1 isPremium,
                                    bits 2.. freeSkillsNumber)
    7+N     '<4Hi': firstNameID, lastNameID, iconID, rank, freeXP  (12 bytes)

Total length is 19 + numSkills.  ``makeCompactDescr`` may append a dossier
compact descriptor; garage crew carries none, and the stock parser ignores
any suffix, so this codec round-trips it verbatim.

Client-side invariants enforced here as well (the stock parser raises on
violations and the offline garage would reject the whole saved file):

- ``header & 15 == 8`` and ``roleLevel <= 100``;
- ``roleID`` names a real role (``SKILL_NAMES[roleID] in ROLES``);
- every skill index names an entry of ``ACTIVE_SKILLS``;
- ``lastSkillLevel <= 100``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

ITEM_TYPE_TANKMAN = 8
MAX_SKILL_LEVEL = 100

# items/components/skills_constants.py in the pinned client, verbatim.
SKILL_NAMES = (
    'reserved', 'commander', 'radioman', 'driver', 'gunner', 'loader',
    'repair', 'fireFighting', 'camouflage', 'brotherhood',
    'reserved', 'reserved', 'reserved', 'reserved', 'reserved', 'reserved',
    'commander_tutor', 'commander_eagleEye', 'commander_sixthSense',
    'commander_expert', 'commander_universalist',
    'reserved', 'reserved', 'reserved', 'reserved', 'reserved', 'reserved',
    'reserved',
    'driver_virtuoso', 'driver_smoothDriving', 'driver_badRoadsKing',
    'driver_rammingMaster', 'driver_tidyPerson',
    'reserved', 'reserved', 'reserved', 'reserved',
    'gunner_gunsmith', 'gunner_sniper', 'gunner_smoothTurret',
    'gunner_rancorous',
    'reserved', 'reserved', 'reserved', 'reserved', 'reserved',
    'loader_pedant', 'loader_desperado', 'loader_intuition',
    'reserved', 'reserved', 'reserved', 'reserved',
    'radioman_inventor', 'radioman_finder', 'radioman_retransmitter',
    'radioman_lastEffort',
    'reserved', 'reserved', 'reserved', 'reserved',
)

SKILL_INDICES = {
    name: index
    for index, name in enumerate(SKILL_NAMES)
    if not name.startswith('reserved')
}

ROLES = frozenset(('commander', 'radioman', 'driver', 'gunner', 'loader'))

COMMON_SKILLS = frozenset(
    ('repair', 'fireFighting', 'camouflage', 'brotherhood'))

SKILLS_BY_ROLES = {
    'commander': COMMON_SKILLS | frozenset((
        'commander_tutor', 'commander_expert', 'commander_universalist',
        'commander_sixthSense', 'commander_eagleEye')),
    'driver': COMMON_SKILLS | frozenset((
        'driver_tidyPerson', 'driver_smoothDriving', 'driver_virtuoso',
        'driver_badRoadsKing', 'driver_rammingMaster')),
    'gunner': COMMON_SKILLS | frozenset((
        'gunner_smoothTurret', 'gunner_sniper', 'gunner_rancorous',
        'gunner_gunsmith')),
    'loader': COMMON_SKILLS | frozenset((
        'loader_pedant', 'loader_desperado', 'loader_intuition')),
    'radioman': COMMON_SKILLS | frozenset((
        'radioman_finder', 'radioman_inventor', 'radioman_lastEffort',
        'radioman_retransmitter')),
}

# skills_constants.ACTIVE_SKILLS: union of every role's skills.
ACTIVE_SKILLS = frozenset().union(*SKILLS_BY_ROLES.values())

_HEADER_SIZE = 5
_TAIL_SIZE = struct.calcsize('<B4Hi')  # flags + 4 uint16 + int32 = 13
FIXED_SIZE = _HEADER_SIZE + 1 + _TAIL_SIZE  # 19, plus one byte per skill


class TankmanFormatError(ValueError):
    """The bytes are not a valid #1513 tankman compact descriptor."""


@dataclass
class Tankman:
    """One parsed crew member, mirroring the client's TankmanDescr fields."""

    nation_id: int
    vehicle_type_id: int
    role: str
    role_level: int
    skills: list = field(default_factory=list)
    last_skill_level: int = 0
    flags: int = 0
    first_name_id: int = 0
    last_name_id: int = 0
    icon_id: int = 0
    rank: int = 0
    free_xp: int = 0
    suffix: bytes = b''

    @property
    def is_female(self) -> bool:
        return bool(self.flags & 1)

    @property
    def is_premium(self) -> bool:
        return bool(self.flags & 2)

    @property
    def free_skills_number(self) -> int:
        return self.flags >> 2

    def skill_names(self) -> list:
        """Learned skills plus the one in training, in learning order."""
        return list(self.skills)

    def trained_skills(self) -> list:
        """Skills actually in effect: all but a not-yet-finished last one."""
        if not self.skills:
            return []
        if self.last_skill_level >= MAX_SKILL_LEVEL:
            return list(self.skills)
        return list(self.skills[:-1])


def parse_tankman(blob: bytes) -> Tankman:
    """Parse one compact descriptor, enforcing the stock parser's checks."""
    if not isinstance(blob, (bytes, bytearray)):
        raise TankmanFormatError('descriptor must be bytes')
    blob = bytes(blob)
    if len(blob) < FIXED_SIZE:
        raise TankmanFormatError(
            'descriptor is %d bytes, need at least %d' % (
                len(blob), FIXED_SIZE))
    header, vehicle_type_id, role_id, role_level, num_skills = struct.unpack(
        '5B', blob[:_HEADER_SIZE])
    if header & 15 != ITEM_TYPE_TANKMAN:
        raise TankmanFormatError('not a tankman descriptor: 0x%02x' % header)
    nation_id = (header >> 4) & 15
    try:
        role = SKILL_NAMES[role_id]
    except IndexError:
        raise TankmanFormatError('unknown role id %d' % role_id)
    if role not in ROLES:
        raise TankmanFormatError('not a role: %r' % role)
    if role_level > MAX_SKILL_LEVEL:
        raise TankmanFormatError('role level %d' % role_level)
    if len(blob) < FIXED_SIZE + num_skills:
        raise TankmanFormatError(
            'descriptor holds %d bytes for %d skills' % (
                len(blob), num_skills))
    skills = []
    for skill_id in blob[_HEADER_SIZE:_HEADER_SIZE + num_skills]:
        try:
            name = SKILL_NAMES[skill_id]
        except IndexError:
            raise TankmanFormatError('unknown skill id %d' % skill_id)
        if name not in ACTIVE_SKILLS:
            raise TankmanFormatError('not an active skill: %r' % name)
        skills.append(name)
    rest = blob[_HEADER_SIZE + num_skills:]
    last_skill_level = rest[0] if num_skills else 0
    if last_skill_level > MAX_SKILL_LEVEL:
        raise TankmanFormatError('last skill level %d' % last_skill_level)
    tail = rest[1:1 + _TAIL_SIZE].ljust(_TAIL_SIZE, b'\x00')
    flags, first_name_id, last_name_id, icon_id, rank, free_xp = (
        struct.unpack('<B4Hi', tail))
    return Tankman(
        nation_id=nation_id,
        vehicle_type_id=vehicle_type_id,
        role=role,
        role_level=role_level,
        skills=skills,
        last_skill_level=last_skill_level,
        flags=flags,
        first_name_id=first_name_id,
        last_name_id=last_name_id,
        icon_id=icon_id,
        rank=rank,
        free_xp=free_xp,
        suffix=rest[1 + _TAIL_SIZE:],
    )


def serialize_tankman(tankman: Tankman) -> bytes:
    """Serialize back to the exact #1513 byte layout."""
    if tankman.role not in ROLES:
        raise TankmanFormatError('not a role: %r' % tankman.role)
    if not 0 <= tankman.role_level <= MAX_SKILL_LEVEL:
        raise TankmanFormatError('role level %d' % tankman.role_level)
    if not 0 <= tankman.last_skill_level <= MAX_SKILL_LEVEL:
        raise TankmanFormatError(
            'last skill level %d' % tankman.last_skill_level)
    header = ITEM_TYPE_TANKMAN + (tankman.nation_id << 4)
    blob = struct.pack(
        '4B', header, tankman.vehicle_type_id,
        SKILL_INDICES[tankman.role], tankman.role_level)
    skill_ids = []
    for name in tankman.skills:
        try:
            skill_ids.append(SKILL_INDICES[name])
        except KeyError:
            raise TankmanFormatError('unknown skill %r' % name)
    blob += struct.pack(
        '%dB' % (1 + len(skill_ids)), len(skill_ids), *skill_ids)
    blob += bytes((tankman.last_skill_level if skill_ids else 0,))
    blob += struct.pack(
        '<B4Hi', tankman.flags, tankman.first_name_id, tankman.last_name_id,
        tankman.icon_id, tankman.rank, tankman.free_xp)
    blob += tankman.suffix
    return blob


def with_skills(
        tankman: Tankman, trained: list, training: str | None) -> Tankman:
    """Return a copy with ``trained`` skills at 100% plus one in training.

    The byte-level shape matches the stock client state "N trained skills and
    one selected at 0%": every trained skill comes first, the in-training
    skill last, and ``lastSkillLevel`` is 0.  All other fields (identity,
    flags, passport ids, rank, free XP, suffix) are preserved.
    """
    skills = list(trained) + ([training] if training else [])
    last_skill_level = 0 if training else (
        MAX_SKILL_LEVEL if skills else 0)
    return Tankman(
        nation_id=tankman.nation_id,
        vehicle_type_id=tankman.vehicle_type_id,
        role=tankman.role,
        role_level=tankman.role_level,
        skills=skills,
        last_skill_level=last_skill_level,
        flags=tankman.flags,
        first_name_id=tankman.first_name_id,
        last_name_id=tankman.last_name_id,
        icon_id=tankman.icon_id,
        rank=tankman.rank,
        free_xp=tankman.free_xp,
        suffix=tankman.suffix,
    )
