def plural(count: int, one: str, few: str, many: str) -> str:
    """Форма слова после числа: 1 компания, 2 компании, 5 компаний, 21 компания."""
    tail = abs(count) % 100
    if 11 <= tail <= 14:
        return many
    if tail % 10 == 1:
        return one
    return few if 2 <= tail % 10 <= 4 else many


def decimal(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")
