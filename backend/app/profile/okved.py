"""Справочник ОКВЭД 2 (ОК 029-2014): код → раздел, класс, укрупнённый сегмент и темы новостей.

Разделы и классы — из классификатора, формулировки классов сокращены. Сегменты — наша группировка
по рынкам: компании одного сегмента реагируют на одни и те же новости, будь то производитель,
оптовик или розница. Роль в цепочке (`ValueChain`) отделяет одно от другого.
"""

import re
from dataclasses import dataclass

from app.contracts.news import Topic
from app.contracts.profile import Industry, IndustryResolution, ValueChain

CODE = re.compile(r"^\d{2}(\.\d{1,2}){0,2}$")


@dataclass(frozen=True)
class Section:
    letter: str
    name: str
    classes: range
    value_chain: ValueChain


@dataclass(frozen=True)
class Segment:
    id: str
    name: str
    keywords: tuple[str, ...]
    topics: tuple[Topic, ...]


SECTIONS = (
    Section(
        "A",
        "Сельское, лесное хозяйство, охота, рыболовство и рыбоводство",
        range(1, 4),
        ValueChain.EXTRACTION,
    ),
    Section("B", "Добыча полезных ископаемых", range(5, 10), ValueChain.EXTRACTION),
    Section("C", "Обрабатывающие производства", range(10, 34), ValueChain.MANUFACTURING),
    Section(
        "D",
        "Обеспечение электрической энергией, газом и паром",
        range(35, 36),
        ValueChain.UTILITIES,
    ),
    Section(
        "E",
        "Водоснабжение, водоотведение, сбор и утилизация отходов",
        range(36, 40),
        ValueChain.UTILITIES,
    ),
    Section("F", "Строительство", range(41, 44), ValueChain.CONSTRUCTION),
    Section(
        "G",
        "Торговля оптовая и розничная; ремонт автотранспортных средств",
        range(45, 48),
        ValueChain.WHOLESALE,
    ),
    Section("H", "Транспортировка и хранение", range(49, 54), ValueChain.SERVICES),
    Section(
        "I",
        "Деятельность гостиниц и предприятий общественного питания",
        range(55, 57),
        ValueChain.SERVICES,
    ),
    Section("J", "Деятельность в области информации и связи", range(58, 64), ValueChain.SERVICES),
    Section("K", "Деятельность финансовая и страховая", range(64, 67), ValueChain.SERVICES),
    Section(
        "L", "Деятельность по операциям с недвижимым имуществом", range(68, 69), ValueChain.SERVICES
    ),
    Section(
        "M",
        "Деятельность профессиональная, научная и техническая",
        range(69, 76),
        ValueChain.SERVICES,
    ),
    Section(
        "N",
        "Деятельность административная и сопутствующие услуги",
        range(77, 83),
        ValueChain.SERVICES,
    ),
    Section(
        "O",
        "Государственное управление и военная безопасность; социальное обеспечение",
        range(84, 85),
        ValueChain.SERVICES,
    ),
    Section("P", "Образование", range(85, 86), ValueChain.SERVICES),
    Section(
        "Q",
        "Деятельность в области здравоохранения и социальных услуг",
        range(86, 89),
        ValueChain.SERVICES,
    ),
    Section(
        "R",
        "Деятельность в области культуры, спорта, досуга и развлечений",
        range(90, 94),
        ValueChain.SERVICES,
    ),
    Section("S", "Предоставление прочих видов услуг", range(94, 97), ValueChain.SERVICES),
    Section(
        "T", "Деятельность домашних хозяйств как работодателей", range(97, 99), ValueChain.SERVICES
    ),
    Section(
        "U", "Деятельность экстерриториальных организаций", range(99, 100), ValueChain.SERVICES
    ),
)

CLASSES = {
    "01": "Растениеводство и животноводство",
    "02": "Лесоводство и лесозаготовки",
    "03": "Рыболовство и рыбоводство",
    "05": "Добыча угля",
    "06": "Добыча нефти и природного газа",
    "07": "Добыча металлических руд",
    "08": "Добыча прочих полезных ископаемых",
    "09": "Услуги в области добычи полезных ископаемых",
    "10": "Производство пищевых продуктов",
    "11": "Производство напитков",
    "12": "Производство табачных изделий",
    "13": "Производство текстильных изделий",
    "14": "Производство одежды",
    "15": "Производство кожи и изделий из кожи",
    "16": "Обработка древесины и производство изделий из дерева",
    "17": "Производство бумаги и бумажных изделий",
    "18": "Полиграфическая деятельность",
    "19": "Производство кокса и нефтепродуктов",
    "20": "Производство химических веществ и продуктов",
    "21": "Производство лекарственных средств и медицинских материалов",
    "22": "Производство резиновых и пластмассовых изделий",
    "23": "Производство прочей неметаллической минеральной продукции",
    "24": "Металлургическое производство",
    "25": "Производство готовых металлических изделий",
    "26": "Производство компьютеров, электронных и оптических изделий",
    "27": "Производство электрического оборудования",
    "28": "Производство машин и оборудования",
    "29": "Производство автотранспортных средств",
    "30": "Производство прочих транспортных средств",
    "31": "Производство мебели",
    "32": "Производство прочих готовых изделий",
    "33": "Ремонт и монтаж машин и оборудования",
    "35": "Обеспечение электроэнергией, газом и паром",
    "36": "Забор, очистка и распределение воды",
    "37": "Сбор и обработка сточных вод",
    "38": "Сбор, обработка и утилизация отходов",
    "39": "Ликвидация последствий загрязнений",
    "41": "Строительство зданий",
    "42": "Строительство инженерных сооружений",
    "43": "Специализированные строительные работы",
    "45": "Торговля автотранспортными средствами и их ремонт",
    "46": "Оптовая торговля",
    "47": "Розничная торговля",
    "49": "Сухопутный и трубопроводный транспорт",
    "50": "Водный транспорт",
    "51": "Воздушный и космический транспорт",
    "52": "Складское хозяйство и вспомогательная транспортная деятельность",
    "53": "Почтовая связь и курьерская деятельность",
    "55": "Временное проживание",
    "56": "Общественное питание",
    "58": "Издательская деятельность",
    "59": "Производство кино- и видеофильмов, телепрограмм, звукозаписей",
    "60": "Телевизионное и радиовещание",
    "61": "Телекоммуникации",
    "62": "Разработка программного обеспечения и IT-консалтинг",
    "63": "Деятельность в области информационных технологий",
    "64": "Финансовые услуги",
    "65": "Страхование и негосударственные пенсионные фонды",
    "66": "Вспомогательная деятельность в сфере финансов и страхования",
    "68": "Операции с недвижимым имуществом",
    "69": "Право и бухгалтерский учёт",
    "70": "Головные офисы; консультирование по вопросам управления",
    "71": "Архитектура, инженерное проектирование, технические испытания",
    "72": "Научные исследования и разработки",
    "73": "Реклама и исследование рынка",
    "74": "Прочая профессиональная, научная и техническая деятельность",
    "75": "Ветеринарная деятельность",
    "77": "Аренда и лизинг",
    "78": "Трудоустройство и подбор персонала",
    "79": "Туристические агентства и туроператоры",
    "80": "Обеспечение безопасности и расследования",
    "81": "Обслуживание зданий и территорий",
    "82": "Административно-хозяйственные и прочие услуги для бизнеса",
    "84": "Государственное управление",
    "85": "Образование",
    "86": "Здравоохранение",
    "87": "Уход с обеспечением проживания",
    "88": "Социальные услуги без обеспечения проживания",
    "90": "Творческая деятельность, искусство и развлечения",
    "91": "Библиотеки, архивы, музеи",
    "92": "Азартные игры и лотереи",
    "93": "Спорт, отдых и развлечения",
    "94": "Общественные и некоммерческие организации",
    "95": "Ремонт компьютеров и предметов личного потребления",
    "96": "Прочие персональные услуги",
    "97": "Домашние хозяйства с наёмными работниками",
    "98": "Производство товаров и услуг домашними хозяйствами для себя",
    "99": "Экстерриториальные организации",
}

T = Topic
SEGMENTS = {
    segment.id: segment
    for segment in (
        Segment(
            "agro",
            "Сельское хозяйство и рыболовство",
            (
                "сельское хозяйство",
                "урожай",
                "зерно",
                "экспорт зерна",
                "удобрения",
                "агрохолдинг",
                "рыболовство",
                "квоты на вылов",
                "россельхознадзор",
            ),
            (T.COMMODITIES, T.TRADE, T.FX, T.REGULATION),
        ),
        Segment(
            "oil_gas",
            "Нефть, газ и нефтепродукты",
            (
                "нефть",
                "газ",
                "brent",
                "urals",
                "нефтепродукты",
                "нпз",
                "топливо",
                "бензин",
                "демпфер",
                "опек",
            ),
            (T.COMMODITIES, T.FX, T.SANCTIONS, T.TAXES),
        ),
        Segment(
            "mining_metals",
            "Добыча и металлургия",
            (
                "добыча",
                "уголь",
                "руда",
                "металлургия",
                "сталь",
                "металлопрокат",
                "алюминий",
                "никель",
                "медь",
                "золото",
            ),
            (T.COMMODITIES, T.FX, T.SANCTIONS, T.TRADE),
        ),
        Segment(
            "food",
            "Продукты питания и напитки",
            (
                "продукты питания",
                "продовольствие",
                "пищевая промышленность",
                "цены на продукты",
                "импорт продовольствия",
                "мясо",
                "рыба",
                "морепродукты",
                "молочная продукция",
                "напитки",
            ),
            (T.INFLATION, T.CONSUMPTION, T.TRADE, T.REGULATION),
        ),
        Segment(
            "consumer_goods",
            "Одежда, обувь и товары для дома",
            (
                "одежда",
                "обувь",
                "текстиль",
                "мебель",
                "бытовые товары",
                "товары для дома",
                "маркировка",
                "маркетплейсы",
            ),
            (T.CONSUMPTION, T.FX, T.TRADE),
        ),
        Segment(
            "wood_paper",
            "Лес, бумага и упаковка",
            (
                "лесная промышленность",
                "древесина",
                "пиломатериалы",
                "целлюлоза",
                "бумага",
                "упаковка",
                "полиграфия",
            ),
            (T.COMMODITIES, T.TRADE, T.FX),
        ),
        Segment(
            "chemicals",
            "Химия и пластики",
            (
                "химическая промышленность",
                "нефтехимия",
                "полимеры",
                "пластик",
                "резина",
                "минеральные удобрения",
                "лакокрасочные материалы",
            ),
            (T.COMMODITIES, T.FX, T.SANCTIONS),
        ),
        Segment(
            "healthcare",
            "Здравоохранение и фармацевтика",
            (
                "лекарства",
                "фармацевтика",
                "медицинские изделия",
                "медицинское оборудование",
                "росздравнадзор",
                "госзакупки",
                "клиники",
                "аптеки",
                "импортозамещение",
            ),
            (T.REGULATION, T.FX, T.TRADE),
        ),
        Segment(
            "building_materials",
            "Стройматериалы",
            (
                "стройматериалы",
                "цемент",
                "бетон",
                "кирпич",
                "стекло",
                "керамика",
                "строительный рынок",
            ),
            (T.KEY_RATE, T.COMMODITIES),
        ),
        Segment(
            "machinery",
            "Машиностроение и металлоизделия",
            (
                "машиностроение",
                "оборудование",
                "станки",
                "металлоизделия",
                "автопром",
                "комплектующие",
                "импортозамещение",
                "оборонный заказ",
            ),
            (T.KEY_RATE, T.FX, T.SANCTIONS, T.TRADE),
        ),
        Segment(
            "electronics",
            "Электроника и электротехника",
            (
                "электроника",
                "микроэлектроника",
                "полупроводники",
                "электротехника",
                "кабели",
                "приборостроение",
                "параллельный импорт",
            ),
            (T.SANCTIONS, T.TRADE, T.FX),
        ),
        Segment(
            "utilities",
            "Энергетика и ЖКХ",
            (
                "электроэнергия",
                "энергетика",
                "тарифы",
                "жкх",
                "теплоснабжение",
                "водоснабжение",
                "вывоз отходов",
                "переработка отходов",
            ),
            (T.REGULATION, T.INFLATION),
        ),
        Segment(
            "construction",
            "Строительство и девелопмент",
            (
                "строительство",
                "застройщик",
                "девелопер",
                "ипотека",
                "жилищное строительство",
                "эскроу",
                "инфраструктура",
                "госзаказ",
                "подрядчик",
            ),
            (T.KEY_RATE, T.REGULATION, T.DEBT_MARKET),
        ),
        Segment(
            "real_estate",
            "Недвижимость и аренда",
            (
                "недвижимость",
                "коммерческая недвижимость",
                "аренда",
                "ставки аренды",
                "офисы",
                "склады",
                "торговые центры",
                "управление недвижимостью",
            ),
            (T.KEY_RATE, T.CONSUMPTION, T.DEBT_MARKET),
        ),
        Segment(
            "auto",
            "Автомобили и автосервис",
            (
                "автомобили",
                "авторынок",
                "автодилер",
                "автокредит",
                "утильсбор",
                "запчасти",
                "автосервис",
            ),
            (T.KEY_RATE, T.FX, T.TRADE, T.REGULATION),
        ),
        Segment(
            "trade",
            "Торговля широкого профиля",
            (
                "оптовая торговля",
                "дистрибуция",
                "дистрибьютор",
                "импорт",
                "поставки",
                "параллельный импорт",
                "таможенные пошлины",
            ),
            (T.FX, T.TRADE, T.KEY_RATE),
        ),
        Segment(
            "retail",
            "Розничная торговля",
            (
                "розничная торговля",
                "ритейл",
                "торговые сети",
                "потребительский спрос",
                "маркетплейсы",
                "онлайн-торговля",
            ),
            (T.CONSUMPTION, T.INFLATION, T.KEY_RATE),
        ),
        Segment(
            "logistics",
            "Транспорт и логистика",
            (
                "логистика",
                "грузоперевозки",
                "перевозки",
                "железная дорога",
                "ржд",
                "порты",
                "авиаперевозки",
                "складская логистика",
                "тарифы на перевозки",
            ),
            (T.TRADE, T.COMMODITIES, T.SANCTIONS),
        ),
        Segment(
            "hospitality",
            "Гостиницы, общепит и туризм",
            ("рестораны", "общепит", "доставка еды", "гостиницы", "туризм", "турпоток"),
            (T.CONSUMPTION, T.INFLATION, T.LABOR),
        ),
        Segment(
            "it_telecom",
            "IT и телекоммуникации",
            (
                "it",
                "информационные технологии",
                "программное обеспечение",
                "разработка по",
                "импортозамещение по",
                "аккредитация it",
                "льготы it",
                "телеком",
                "связь",
                "кибербезопасность",
            ),
            (T.LABOR, T.TAXES, T.REGULATION),
        ),
        Segment(
            "media",
            "Медиа и реклама",
            ("сми", "медиа", "издательство", "кино", "телевидение", "реклама", "рекламный рынок"),
            (T.CONSUMPTION, T.REGULATION),
        ),
        Segment(
            "finance",
            "Финансы и страхование",
            ("банки", "финансовый рынок", "страхование", "лизинг", "кредитование", "банк россии"),
            (T.KEY_RATE, T.REGULATION, T.DEBT_MARKET),
        ),
        Segment(
            "business_services",
            "Профессиональные и деловые услуги",
            (
                "консалтинг",
                "аудит",
                "юридические услуги",
                "инжиниринг",
                "проектирование",
                "научные исследования",
                "подбор персонала",
                "аутсорсинг",
                "аренда оборудования",
            ),
            (T.LABOR, T.MACRO),
        ),
        Segment(
            "social",
            "Госсектор, образование и социальные услуги",
            ("образование", "социальные услуги", "бюджет", "госзакупки", "нацпроекты"),
            (T.REGULATION, T.TAXES),
        ),
        Segment(
            "leisure",
            "Культура, спорт и развлечения",
            ("культура", "спорт", "развлечения", "мероприятия", "фитнес"),
            (T.CONSUMPTION,),
        ),
        Segment(
            "personal_services",
            "Бытовые и прочие услуги",
            ("бытовые услуги", "ремонт", "персональные услуги", "некоммерческие организации"),
            (T.CONSUMPTION,),
        ),
        Segment("other", "Отрасль не определена", (), ()),
    )
}

SEGMENT_BY_CLASS = {
    **dict.fromkeys(("01", "03"), "agro"),
    **dict.fromkeys(("02", "16", "17", "18"), "wood_paper"),
    **dict.fromkeys(("05", "07", "08", "09", "24"), "mining_metals"),
    **dict.fromkeys(("06", "19"), "oil_gas"),
    **dict.fromkeys(("10", "11", "12"), "food"),
    **dict.fromkeys(("13", "14", "15", "31", "32"), "consumer_goods"),
    **dict.fromkeys(("20", "22"), "chemicals"),
    **dict.fromkeys(("21", "86"), "healthcare"),
    "23": "building_materials",
    **dict.fromkeys(("25", "28", "29", "30", "33"), "machinery"),
    **dict.fromkeys(("26", "27"), "electronics"),
    **dict.fromkeys(("35", "36", "37", "38", "39"), "utilities"),
    **dict.fromkeys(("41", "42", "43"), "construction"),
    "45": "auto",
    "46": "trade",
    "47": "retail",
    **dict.fromkeys(("49", "50", "51", "52", "53"), "logistics"),
    **dict.fromkeys(("55", "56", "79"), "hospitality"),
    **dict.fromkeys(("58", "59", "60", "73"), "media"),
    **dict.fromkeys(("61", "62", "63"), "it_telecom"),
    **dict.fromkeys(("64", "65", "66"), "finance"),
    **dict.fromkeys(("68", "81"), "real_estate"),
    **dict.fromkeys(
        ("69", "70", "71", "72", "74", "75", "77", "78", "80", "82"), "business_services"
    ),
    **dict.fromkeys(("84", "85", "87", "88"), "social"),
    **dict.fromkeys(("90", "91", "92", "93"), "leisure"),
    **dict.fromkeys(("94", "95", "96", "97", "98", "99"), "personal_services"),
}

# Уточнения по группам: оптовик продуктами живёт новостями рынка продуктов, а не «торговли вообще».
SEGMENT_BY_PREFIX = {
    "09.1": "oil_gas",
    "26.6": "healthcare",
    "32.5": "healthcare",
    "46.2": "agro",
    "46.3": "food",
    "46.4": "consumer_goods",
    "46.46": "healthcare",
    "46.5": "electronics",
    "46.6": "machinery",
    "46.71": "oil_gas",
    "46.72": "mining_metals",
    "46.73": "building_materials",
    "46.75": "chemicals",
    "47.11": "food",
    "47.2": "food",
    "47.3": "oil_gas",
    "47.73": "healthcare",
    "47.74": "healthcare",
    "77.1": "auto",
}

PREFIXES_LONGEST_FIRST = sorted(SEGMENT_BY_PREFIX, key=len, reverse=True)

RETAIL_CLASSES = {"47"}

# Если код пустой или битый, отрасль угадываем по названию вида деятельности.
CLASS_BY_NAME = (
    (re.compile(r"торговля оптовая|оптовая торговля"), "46"),
    (re.compile(r"торговля розничная|розничная торговля"), "47"),
    (re.compile(r"программн|информационных технологий"), "62"),
    (re.compile(r"ресторан|общественного питания|доставке продуктов питания"), "56"),
    (re.compile(r"гостиниц|временного проживания"), "55"),
    (re.compile(r"недвижим"), "68"),
    (re.compile(r"застройщик|строительство"), "41"),
    (re.compile(r"производство пищевых|производство продуктов"), "10"),
    (re.compile(r"перевоз|транспорт"), "49"),
    (re.compile(r"складск"), "52"),
    (re.compile(r"реклам"), "73"),
    (re.compile(r"консультирован"), "70"),
    (re.compile(r"образовани|обучени"), "85"),
    (re.compile(r"медицин|здравоохранен"), "86"),
    (re.compile(r"добыча нефти|добыча газа"), "06"),
    (re.compile(r"сельск|растениеводств|животноводств"), "01"),
)


@dataclass(frozen=True)
class OkvedInfo:
    code: str | None
    class_code: str | None
    class_name: str | None
    section: Section | None
    segment: Segment
    value_chain: ValueChain | None
    resolution: IndustryResolution

    @property
    def keywords(self) -> list[str]:
        return list(self.segment.keywords)


def normalize_code(raw: str | None) -> str | None:
    if raw is None:
        return None
    code = raw.strip().replace(",", ".").strip(".")
    head, _, tail = code.partition(".")
    if len(head) == 1 and head.isdigit():
        code = f"0{head}" + (f".{tail}" if tail else "")
    return code if CODE.match(code) else None


def section_of(class_code: str) -> Section | None:
    number = int(class_code)
    return next((section for section in SECTIONS if number in section.classes), None)


def segment_of(code: str) -> Segment:
    for prefix in PREFIXES_LONGEST_FIRST:
        if code.startswith(prefix):
            return SEGMENTS[SEGMENT_BY_PREFIX[prefix]]
    return SEGMENTS[SEGMENT_BY_CLASS.get(code[:2], "other")]


def by_class(code: str | None, class_code: str, resolution: IndustryResolution) -> OkvedInfo:
    section = section_of(class_code)
    if section is None or class_code not in CLASSES:
        return unknown(code)
    value_chain = ValueChain.RETAIL if class_code in RETAIL_CLASSES else section.value_chain
    return OkvedInfo(
        code=code,
        class_code=class_code,
        class_name=CLASSES[class_code],
        section=section,
        segment=segment_of(code if code and resolution is IndustryResolution.CODE else class_code),
        value_chain=value_chain,
        resolution=resolution,
    )


def unknown(code: str | None) -> OkvedInfo:
    return OkvedInfo(
        code=code,
        class_code=None,
        class_name=None,
        section=None,
        segment=SEGMENTS["other"],
        value_chain=None,
        resolution=IndustryResolution.UNKNOWN,
    )


def resolve(code: str | None, name: str | None = None) -> OkvedInfo:
    """Отрасль по коду ОКВЭД, а если код пустой, битый или неизвестный — по названию."""
    normalized = normalize_code(code)
    if normalized is not None:
        info = by_class(normalized, normalized[:2], IndustryResolution.CODE)
        if info.resolution is IndustryResolution.CODE:
            return info
    lowered = (name or "").lower()
    for pattern, class_code in CLASS_BY_NAME:
        if pattern.search(lowered):
            return by_class(normalized, class_code, IndustryResolution.NAME)
    return unknown(normalized)


def industry(code: str | None, name: str | None = None) -> Industry:
    info = resolve(code, name)
    return Industry(
        okved_code=info.code,
        okved_name=(name or "").strip() or info.class_name,
        section=info.section.letter if info.section else None,
        segment=info.segment.id,
        segment_name=info.segment.name,
        value_chain=info.value_chain,
        resolved_by=info.resolution,
        topics=list(info.segment.topics),
        keywords=info.keywords,
    )
