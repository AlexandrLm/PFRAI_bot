from aiogram import Bot

MAX_MESSAGE_LENGTH = 4096


def split_long_message(text: str) -> list[str]:
    """
    Splits a long message into multiple smaller ones.
    Tries to split by newlines to keep formatting and avoid breaking HTML tags.
    """
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    parts = []
    current_part = ""
    # Разделяем по строкам, чтобы сохранить форматирование
    lines = text.split('\n')
    
    for line in lines:
        # Если добавление следующей строки превысит лимит
        if len(current_part) + len(line) + 1 > MAX_MESSAGE_LENGTH:
            # Отправляем текущую часть
            parts.append(current_part)
            current_part = line + '\n'
        else:
            current_part += line + '\n'

    # Добавляем последнюю оставшуюся часть
    if current_part:
        parts.append(current_part)
        
    return parts 