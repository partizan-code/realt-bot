"""Консервативное извлечение параметров и безопасный HTML поста из выдачи."""

import re
from decimal import Decimal, InvalidOperation
from html import escape, unescape
from urllib.parse import urlsplit

import config

NUMBER = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[,.]\d{1,2})?"


def raw_text(ad, name):
    value = ad.get(name, [])
    return "\n".join(value) if isinstance(value, list) else str(value or "")


def unique(matches):
    values = set(matches)
    return values.pop() if len(values) == 1 else None


def number(value):
    try:
        return Decimal(re.sub(r"\s", "", value).replace(",", "."))
    except InvalidOperation:
        return None


def parameters(ad, city):
    """Сырые поля остаются в ad; нормализованные данные и доказательства — отдельно."""
    title = raw_text(ad, "title")
    price = raw_text(ad, "price_byn")
    description = raw_text(ad, "description")
    source = "\n".join((raw_text(ad, "address"), description))
    rooms = unique(int(x) for x in re.findall(r"(?<!\d)(\d{1,2})\s*[-–]?\s*комн\b", title, re.I))
    if re.search(r"\d\s*[-–/]\s*\d+\s*[-–]?\s*комн", title, re.I):
        rooms = None
    areas = re.findall(rf"(?<![\d.,])({NUMBER})\s*м[²2]", title)
    area = unique(number(x) for x in areas)
    if re.search(r"\d\s*[-–/]\s*\d+(?:[,.]\d+)?\s*м[²2]", title):
        area = None
    floors = re.findall(r"(?<!\d)(\d{1,3})\s*/\s*(\d{1,3})\s*эт", title, re.I)
    floor = unique((int(a), int(b)) for a, b in floors)
    if floor and not 0 < floor[0] <= floor[1]:
        floor = None
    price_match = re.fullmatch(
        rf"\s*({NUMBER})\s*(?:BYN|бел\.?\s*руб\.?|руб\.?|р\.)\s*"
        r"(?:/\s*мес\.?|в\s+месяц|за\s+месяц)\s*",
        price,
        re.I,
    )
    monthly_price = number(price_match[1]) if price_match else None
    district = unique(
        name
        for name in config.DISTRICTS.get(city, ())
        if re.search(rf"\b{re.escape(name)}\s+(?:район|р-н)\b", source, re.I)
        or re.search(rf"\bрайон\s*:\s*{re.escape(name)}\b", source, re.I)
    )
    evidence = {}
    candidates = {}
    for field, labels in config.RENT_CONDITION_LABELS.items():
        label = "|".join(re.escape(x) for x in labels)
        values = re.findall(
            rf"(?:^|[\n;.!?])\s*(?:{label})\s*:\s*(.+?)(?=[\n;]|[.!?](?:\s|$)|$)",
            description,
            re.I,
        )
        # Несколько несовпадающих утверждений — неизвестное значение.
        candidates[field] = [x.strip() for x in values if x.strip()]
        value = unique(candidates[field])
        evidence[field] = value
    # Отдельные недвусмысленные фразы без меток также сохраняются дословно.
    phrases = {
        "commission": r"без комиссии",
        "deposit": r"без залога",
        "pets": r"(?:с животными (?:нельзя|можно)|без животных)",
    }
    for field, pattern in phrases.items():
        found = re.findall(rf"(?:^|[\n;.!?])\s*({pattern})\s*(?=$|[\n;.!?])", description, re.I)
        if found:
            evidence[field] = unique([*found, *candidates[field]])
    metro = unique(re.findall(r"(?:^|[\n;])\s*Метро\s*:\s*([^\n;.!?]+)", source, re.I))
    return {
        "price_byn_month": monthly_price if monthly_price and monthly_price > 0 else None,
        "rooms": rooms if rooms and rooms > 0 else None,
        "area_m2": area if area and area > 0 else None,
        "floor": floor,
        "district": district,
        "metro": metro.strip() if metro else None,
        **evidence,
    }


def hashtags(data):
    tags = []
    if data["rooms"] is not None:
        tags.append(f"#комнат_{data['rooms']}")
    price = data["price_byn_month"]
    if price is not None:
        tag = next(
            (tag for upper, tag in config.PRICE_TAG_BANDS if price < upper), config.PRICE_TAG_LAST
        )
        tags.append(f"#{tag}")
    if data["district"]:
        tags.append("#район_" + data["district"])
    return " ".join(tags)


def display(value):
    return (
        format(value, "f").rstrip("0").rstrip(".")
        if isinstance(value, Decimal) and value % 1
        else str(value)
    )


def telegram_length(text):
    """Консервативный подсчёт UTF-16 после разбора наших HTML-тегов."""
    return len(unescape(re.sub(r"<[^>]*>", "", text)).encode("utf-16-le")) // 2


def render_post(ad, city, limit=None):
    limit = config.TELEGRAM_TEXT_LIMIT if limit is None else limit
    data = parameters(ad, city)
    heading = []
    if data["rooms"]:
        heading.append(f"{data['rooms']}-комн.")
    if data["area_m2"]:
        heading.append(f"{display(data['area_m2'])} м²")
    heading.append(
        f"{display(data['price_byn_month'])} BYN/мес."
        if data["price_byn_month"]
        else "цена за месяц не указана"
    )
    lines = ["<b>" + escape(" · ".join(heading)) + "</b>"]
    conditions = []
    for field, label in config.POST_CONDITION_NAMES.items():
        value = data[field] or "не указано"
        if len(value) > config.POST_CONDITION_LIMIT:
            value = "подробности в объявлении"
        conditions.append(f"{label}: {value}")
    link = ad.get("link", "")
    parsed = urlsplit(link)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Недопустимая ссылка объявления")
    footer = (
        f'<a href="{escape(link, quote=True)}">Объявление на Kufar</a>\n'
        f"Размещено: {ad['time'].strftime(config.TELEGRAM_TIME_FORMAT)}\n"
        + hashtags(data)
        + "\n"
        + config.SIGNATURE
    )
    # Обязательные условия и ссылка не теряются при сокращении описания.
    base = "\n".join([*lines, *(escape(x) for x in conditions), footer])
    if telegram_length(base) > limit:
        return None  # Вызывающий код выберет более вместительное текстовое сообщение.
    optional = []
    address = raw_text(ad, "address").strip()
    if address:
        optional.append("Адрес: " + address)
    if data["district"] and data["district"].casefold() not in address.casefold():
        optional.append("Район: " + data["district"])
    if data["metro"] and data["metro"].casefold() not in address.casefold():
        optional.append("Метро: " + data["metro"])
    if data["floor"]:
        optional.append("Этаж: {}/{}".format(*data["floor"]))
    # Неизвестные структурированные поля не уничтожают исходные параметры и цену.
    if any(data[key] is None for key in ("rooms", "area_m2", "floor")):
        optional.append(raw_text(ad, "title"))
    if data["price_byn_month"] is None and raw_text(ad, "price_byn"):
        optional.append("Цена в источнике: " + raw_text(ad, "price_byn"))
    extras = []
    for value in optional:
        if value and value.casefold() not in {x.casefold() for x in extras}:
            candidate = "\n".join(
                [*lines, *(escape(x) for x in [*extras, value, *conditions]), footer]
            )
            if telegram_length(candidate) <= limit:
                extras.append(value)
    sentences = re.split(r"(?<=[.!?])\s+|[\n;]+", raw_text(ad, "description"))
    description = []
    for sentence in sentences:
        sentence = " ".join(sentence.split())
        label, separator, value = sentence.partition(":")
        if separator:
            known = {
                alias.casefold(): data[field]
                for field, aliases in config.RENT_CONDITION_LABELS.items()
                for alias in aliases
            }
            known.update({"район": data["district"], "метро": data["metro"]})
            fact = known.get(label.strip().casefold())
            if fact and value.strip().rstrip(".!?").casefold() == fact.rstrip(".!?").casefold():
                continue
        if not sentence or sentence.casefold().rstrip(".!?") in {
            x.casefold().rstrip(".!?") for x in [*extras, *conditions, *description]
        }:
            continue
        candidate_description = " ".join([*description, sentence])
        candidate = "\n".join(
            [*lines, *(escape(x) for x in [*extras, *conditions, candidate_description]), footer]
        )
        if (
            len(candidate_description) <= config.POST_DESCRIPTION_LIMIT
            and telegram_length(candidate) <= limit
        ):
            description.append(sentence)
    return "\n".join(
        [
            *lines,
            *(escape(x) for x in [*extras, *conditions]),
            *([escape(" ".join(description))] if description else []),
            footer,
        ]
    )
