"""Shared prompt content; runtimes decide how snapshots and updates are delivered."""
from __future__ import annotations


def identity_sections(*, base: str, conversation: str, persona: str = '',
                      user_profile: str = '', soul: str = '', session: str = '') -> dict[str, str]:
    return {'base_rules': base.replace('{chat_rules}', conversation), 'persona': persona,
            'user_profile': user_profile, 'soul': soul, 'session': session}


def join_sections(sections: dict[str, str]) -> str:
    return '\n\n'.join(value.strip() for value in sections.values() if value and value.strip())


def render_team_skill_context(teams, skills_listing: str = '') -> str:
    names = sorted({str(team).strip() for team in teams if str(team).strip()})
    parts = []
    if names:
        parts.append('【所属 Teams】\n' + '\n'.join(f'team: {team}' for team in names))
    if skills_listing.strip():
        parts.append(skills_listing.strip())
    return '\n\n'.join(parts)


def render_section_updates(previous: dict[str, str], current: dict[str, str]) -> str:
    updates = [f"【本轮 {name}】\n{value or '此前提供的此项信息已撤销。'}"
               for name, value in current.items() if previous.get(name, '') != value]
    updates.extend(f'【本轮 {name}】\n此前提供的此项信息已撤销。'
                   for name in previous if name not in current and previous[name])
    return '\n\n'.join(updates)
