"""Seven relative effort levels, mapped only to advertised native choices."""
LEVEL_NAMES = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
_ALIASES = {'off':'none', 'disabled':'none', 'min':'minimal', 'med':'medium',
            'extra_high':'xhigh', 'extra-high':'xhigh', 'maximum':'max', 'ultra':'max'}


def native_level(value: str) -> int:
    name = _ALIASES.get(str(value).lower(), str(value).lower())
    return LEVEL_NAMES.index(name) + 1 if name in LEVEL_NAMES else 0


def level_map(choices) -> dict[str, str]:
    """Missing ranks use the nearest supported rank; ties prefer less effort.

    Unknown values and automatic/default choices have no claimed ordering.
    A model with fewer than seven native settings deliberately repeats values.
    """
    ranked = [(native_level(value), value) for value in choices if isinstance(value, str) and native_level(value)]
    return {str(level): min(ranked, key=lambda pair:(abs(pair[0] - level), pair[0]))[1]
            for level in range(1, 8)} if ranked else {}


def mapped_effort(choices, level: int) -> str | None:
    if type(level) is not int or not 1 <= level <= 7:
        return None
    return level_map(choices).get(str(level))
