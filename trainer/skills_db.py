"""Skill metadata for the crew template tool.

Canonical names and role membership come from the pinned client's
``items/components/skills_constants.py`` (re-exported by
``trainer.tankman_codec``); Chinese display names come from the CN client's
own ``res/text/LC_MESSAGES/item_types.mo``.
"""

from __future__ import annotations

from trainer.tankman_codec import (
    ACTIVE_SKILLS, COMMON_SKILLS, ROLES, SKILLS_BY_ROLES)

ROLE_NAMES_CN = {
    'commander': '车长',
    'radioman': '通信兵',
    'driver': '驾驶员',
    'gunner': '炮手',
    'loader': '装填手',
}

SKILL_NAMES_CN = {
    'repair': '修复',
    'fireFighting': '灭火',
    'camouflage': '隐蔽',
    'brotherhood': '兄弟连',
    'commander_tutor': '老兵',
    'commander_expert': '鹰眼',
    'commander_universalist': '多面手',
    'commander_sixthSense': '战场直觉',
    'commander_eagleEye': '侦察',
    'driver_tidyPerson': '引擎保养',
    'driver_smoothDriving': '平稳驾驶',
    'driver_virtuoso': '快速转弯',
    'driver_badRoadsKing': '如履平地',
    'driver_rammingMaster': '专注撞击',
    'gunner_smoothTurret': '人工稳定',
    'gunner_sniper': '致命一击',
    'gunner_rancorous': '复仇女神',
    'gunner_gunsmith': '炮术大师',
    'loader_pedant': '规整弹药',
    'loader_desperado': '破釜沉舟',
    'loader_intuition': '爆发装填',
    'radioman_finder': '态势感知',
    'radioman_inventor': '信号增强',
    'radioman_lastEffort': '最后的电波',
    'radioman_retransmitter': '通联中继',
}


def describe_skill(name):
    """``'driver_badRoadsKing (如履平地)'`` for reports."""
    cn = SKILL_NAMES_CN.get(name)
    return '%s (%s)' % (name, cn) if cn else name


def describe_role(role):
    cn = ROLE_NAMES_CN.get(role)
    return '%s (%s)' % (role, cn) if cn else role


def skills_for_roles(roles):
    """Skills learnable by a tankman holding every role in ``roles``."""
    allowed = set(COMMON_SKILLS)
    for role in roles:
        allowed |= SKILLS_BY_ROLES.get(role, frozenset())
    return allowed


def validate_skill_assignment(roles, skills):
    """Return the list of skills not learnable by this role combination."""
    allowed = skills_for_roles(roles)
    return [skill for skill in skills if skill not in allowed]


__all__ = [
    'ACTIVE_SKILLS', 'COMMON_SKILLS', 'ROLES', 'SKILLS_BY_ROLES',
    'ROLE_NAMES_CN', 'SKILL_NAMES_CN', 'describe_skill', 'describe_role',
    'skills_for_roles', 'validate_skill_assignment',
]
