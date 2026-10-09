"""Демо-клиент: отвечает без ИИ, по правилам.

Суть: чтобы весь интерфейс можно было смотреть и проверять без ключа
API, демо-клиент собирает карточку из того, что движок уже нашёл сам:
  - признаки мошенничества (core/signals.py) — для /развод;
  - анализ ссылок (core/links.py);
  - найденные демо-источники (rag/store.py) — для /правда, /чек, /права;
  - суммы и даты (core/textparse.py).
Поэтому ответы демо-режима не случайны: «карта заблокирована, назовите
код» действительно даст 🔴 с подсветкой этих фраз, а незнакомое
утверждение без источника — честное ⚪ «недостаточно данных».

Каждая карточка демо-режима помечается в notes, что это демо.
"""

from __future__ import annotations

import re
from datetime import date

from backend.app.cards.schema import (
    AgreementDraft,
    DisputeSection,
    DocumentSection,
    Reason,
    RewriteSection,
    SimpleExplanation,
    SourceRef,
    TrainerReply,
    TrainerReview,
    VerdictCard,
)
from backend.app.core.links import LinkReport
from backend.app.core.signals import SignalHit, risk_score
from backend.app.core.textparse import find_deadline, find_money
from backend.app.llm.base import LLMClient, LLMRequest, LLMResult
from backend.app.rag.store import Retrieved, stem, tokenize

STRONG_MATCH = 8.0  # порог «сильного» совпадения с источником (см. rag/store.py)
CLAIM_OVERLAP = 0.4  # какая доля слов разбираемого утверждения должна встретиться в тексте
DEMO_NOTE = "Демо-режим: карточка собрана по правилам, без ИИ. Вставьте LLM_API_KEY в .env для настоящей проверки ИИ."


def claim_overlap(text: str, claim: str) -> float:
    """Доля основ слов утверждения источника, которые есть в проверяемом тексте."""
    claim_tokens = set(tokenize(claim))
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & set(tokenize(text))) / len(claim_tokens)


def _source_ref(r: Retrieved) -> SourceRef:
    s = r.source
    return SourceRef(title=s.title, url=s.url, date=s.date, is_demo=s.is_demo)


def _signal_reasons(hits: list[SignalHit], limit: int = 3) -> list[Reason]:
    return [Reason(text=f"{h.title}. {h.explanation}", quote=h.quotes[0] if h.quotes else "") for h in hits[:limit]]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text.strip())
    return [p.strip() for p in parts if len(p.strip()) > 2]


# ---------------------------------------------------------------- режимы


UNIVERSITY_RE = re.compile(r"пар[аыу]?\b|занят\w*|сесси\w*|рубежк\w*|рубежн\w*|экзамен\w*|пересдач\w*|стипенди\w*|деканат\w*|"
                           r"муит|iitu|универ\w*|семестр\w*|каникул\w*|\bfx\b|\bgpa\b|platonus|общежит\w*", re.IGNORECASE)


def mock_pravda(ctx: dict) -> VerdictCard:
    sources: list[Retrieved] = ctx.get("sources", [])
    manip: list[SignalHit] = ctx.get("manipulation", [])
    # Источник-разбор используем, только если совпадение сильное (не одно общее слово вроде «закон»)
    # и источник разбирает ИМЕННО это утверждение (большая часть слов утверждения есть в тексте).
    text = ctx.get("text", "")
    top = next((r for r in sources if r.source.verdict in ("red", "green", "yellow") and r.score >= STRONG_MATCH
                and claim_overlap(text, r.source.claim) >= CLAIM_OVERLAP), None)

    if top is not None:
        s = top.source
        status = s.verdict
        title = {
            "red": "Похоже на фейк: официального подтверждения нет",
            "green": "Подтверждается источником",
            "yellow": "Частично верно, есть оговорки",
        }[status]
        reasons = [Reason(text=f"Источник разбирает это утверждение: «{s.claim}».")]
        first_sentence = next((x for x in _sentences(s.text) if not x.startswith("⚠️")), "")
        if first_sentence:
            reasons.append(Reason(text=first_sentence))
        reasons += _signal_reasons(manip, 1)
        return VerdictCard(
            status=status, title=title, kind="fact" if status != "yellow" else "manipulation",
            reasons=reasons[:3], sources=[_source_ref(top)], confidence=75,
            do=s.do or ["Проверьте первоисточник на adilet.zan.kz или сайте госоргана"],
            dont=s.dont or ["Не пересылайте дальше, пока не проверили"],
        )

    if ctx.get("opinion") and not manip:
        return VerdictCard(
            status="yellow", title="Это мнение, а не проверяемый факт", kind="opinion",
            reasons=[Reason(text="В тексте оценка или личное мнение — его нельзя подтвердить или опровергнуть источником.")],
            confidence=60, do=["Спросите автора, на каких фактах основано мнение"], dont=["Не выдавайте мнение за факт при пересылке"],
        )

    reasons = [Reason(text="В базе источников нет документа, который подтверждает или опровергает это утверждение.")]
    reasons += _signal_reasons(manip, 2)
    web = [r for r in sources if r.source.id.startswith("web:")]
    if web:
        # Демо без ИИ не делает вывод по найденным статьям — только честно показывает, что нашлось.
        return VerdictCard(
            status="unknown", title="Нашлись публикации — открой и сверь сам",
            kind="manipulation" if manip else "insufficient", confidence=30,
            reasons=[Reason(text="По теме есть публикации в интернете, но без ИИ я не берусь сказать, подтверждают ли они именно это.")]
            + _signal_reasons(manip, 1),
            sources=[_source_ref(r) for r in web[:3]],
            do=["Открой источники ниже и посмотри дату и автора", "Новости университета — на iitu.edu.kz/ru/news и в канале МУИТ"],
            dont=["Не пересылай дальше, пока не убедился"],
        )
    if UNIVERSITY_RE.search(text):
        related = next((r.source for r in sources if r.source.id.startswith(("iitu_", "news_"))), None)
        return VerdictCard(
            status="unknown", title="Официального подтверждения не нашёл — пока это слух",
            kind="manipulation" if manip else "insufficient", reasons=reasons[:3], confidence=30,
            sources=[_source_ref(next(r for r in sources if r.source is related))] if related else [],
            do=["Сверь даты на странице академического календаря МУИТ и в новостях iitu.edu.kz",
                "Расписание с заменами — в Platonus; вопросы — в Офис-регистратор или деканат"],
            dont=["Не пересылай слух в чат потока, пока нет подтверждения"],
        )
    return VerdictCard(
        status="unknown", title="Недостаточно данных: официального источника не нашёл",
        kind="manipulation" if manip else "insufficient", reasons=reasons, confidence=30,
        do=["Найдите первоисточник: законы публикуются на adilet.zan.kz, новости госорганов — на gov.kz",
            "Посмотрите, пишут ли об этом официальные СМИ со ссылкой на документ"],
        dont=["Не пересылайте дальше, пока не нашли источник"],
    )


def mock_razvod(ctx: dict) -> VerdictCard:
    hits: list[SignalHit] = ctx.get("signals", [])
    links: list[LinkReport] = ctx.get("links", [])
    sources: list[Retrieved] = ctx.get("sources", [])
    score = risk_score(hits) + sum(min(link.risk, 4) for link in links)
    bad_links = [link for link in links if link.risk >= 3]

    reasons = _signal_reasons(hits, 2)
    for link in bad_links[:1] or [link for link in links if link.risk > 0][:1]:
        reasons.append(Reason(text=" ".join(link.findings[:2]), quote=link.url))

    ids = {h.id for h in hits}
    do: list[str] = []
    dont: list[str] = []
    if ids & {"code_request", "account_blocked", "fake_authority", "safe_account"}:
        do.append("Прервите разговор и сами позвоните в банк по номеру на обороте карты или в приложении")
        dont += ["Не сообщайте коды из SMS, CVV и PIN", "Не переводите деньги на «безопасный» счёт"]
    if "relative_in_trouble" in ids:
        do.append("Позвоните близкому на старый номер или задайте вопрос, ответ на который знаете только вы")
        dont.append("Не переводите деньги, пока не поговорили с близким голосом")
    if ids & {"guaranteed_income", "job_scam", "prepayment", "prize"}:
        do.append("Проверьте компанию: лицензия, БИН, отзывы вне её сайта")
        dont.append("Не вносите предоплату и «комиссию за вывод»")
    if "remote_access" in ids:
        dont.append("Не устанавливайте приложения по просьбе незнакомцев")
    student = ids & {"exam_answers_sale", "grade_for_money", "fake_fundraising", "iitu_foreign_requisites", "iitu_card_payment"}
    if ids & {"exam_answers_sale", "grade_for_money"}:
        do.append("Не переводи деньги: это либо обман, либо грубое нарушение Кодекса академической честности")
        do.append("О продаже ответов или «оценках за деньги» можно анонимно сообщить в ящик доверия: trust@iitu.edu.kz")
        dont += ["Не покупай «ответы», «сливы» и «закрытие предмета»", "Не пересылай такие предложения в чат группы"]
    if ids & {"fake_fundraising", "iitu_foreign_requisites", "iitu_card_payment"}:
        do.append("Реквизиты для оплаты обучения сверяй только на iitu.edu.kz (Путеводитель студента → Инструкция по оплате)")
        dont.append("Не переводи деньги «за учёбу» на личные карты одногруппников или «кураторов»")
    if links:
        dont.append("Не открывайте ссылку и не вводите на сайте данные карты")
    if not student:
        do.append("Если уже передали данные — заблокируйте карту в официальном приложении банка")

    # 🔴 — сумма признаков большая ИЛИ есть хотя бы один «тяжёлый» признак (вес 3: код из SMS,
    # удалённый доступ, «безопасный счёт», гарантированный доход...) ИЛИ опасная ссылка.
    strong = any(h.weight >= 3 for h in hits)
    if score >= 4 or strong or bad_links:
        status, title, confidence = "red", "Похоже на мошенничество", min(95, 60 + score * 5)
        if "relative_in_trouble" in ids:
            title = "Похоже на мошенничество «родственник в беде»"
        elif ids & {"guaranteed_income"}:
            title = "Похоже на финансовую пирамиду или обман с инвестициями"
        elif ids & {"job_scam"}:
            title = "Похоже на мошенничество с «подработкой»"
        elif ids & {"exam_answers_sale", "grade_for_money"}:
            title = "«Ответы» и «оценки» за деньги — обман или нарушение академической честности"
        elif ids & {"iitu_foreign_requisites", "iitu_card_payment", "fake_fundraising"}:
            title = "Похоже на фейковый сбор или поддельные реквизиты для оплаты"
    elif score >= 1:
        status, title, confidence = "yellow", "Есть признаки давления или обмана — будьте осторожны", 50
    elif links and all(link.is_official for link in links):
        status, title, confidence = "green", "Ссылка ведёт на официальный сайт", 60
        reasons = [Reason(text=link.findings[0], quote=link.url) for link in links[:2]]
        do, dont = ["Всё равно не вводите коды из SMS, если вас об этом просят в переписке"], []
    else:
        status, title, confidence = "unknown", "Явных признаков мошенничества не нашёл", 35
        reasons = [Reason(text="В тексте нет типичных приёмов мошенников, но это не гарантия безопасности.")]
        do, dont = ["Если сомневаетесь — проверьте отправителя через официальные каналы"], ["Не сообщайте коды и данные карты"]

    radar = [r for r in sources if r.source.id.startswith("radar:")]
    if radar:
        # Похожую схему уже присылали в Радар, и модератор её подтвердил — это сильный признак.
        reasons = [Reason(text="Похожую схему уже присылали в «Радар разводов», модератор её подтвердил.")] + reasons
        if status in ("unknown", "yellow"):
            status, title, confidence = "red", "Похоже на схему из Радара разводов", max(confidence, 70)
        dont = (["Не переводи деньги и не называй коды"] + dont)[:4]
    warn_sources = ([_source_ref(r) for r in radar[:1]] + [_source_ref(r) for r in sources if not r.source.verdict])[:2] if status in ("red", "yellow") else []
    return VerdictCard(status=status, title=title, kind="scam", reasons=reasons[:3], sources=warn_sources,
                       confidence=confidence, do=list(dict.fromkeys(do))[:4], dont=list(dict.fromkeys(dont))[:4])


DOC_TYPES = (
    ("fine", r"штраф|постановлени\w*|административн\w*|пдд|камер\w*", "Похоже на штраф или постановление"),
    ("tax", r"налог\w*|кгд|налогов\w*", "Похоже на налоговое уведомление"),
    ("utility", r"квитанц\w*|коммунальн\w*|жкх|ерц|лицев\w+\s+сч[её]т|электроэнерги\w*|отоплени\w*|водоснабжени\w*", "Похоже на квитанцию за коммунальные услуги"),
    ("subscription", r"подписк\w*|автоплат\w*|списани\w*\s+ежемесячн\w*|пробн\w+\s+период", "Похоже на платную подписку"),
    ("court", r"суд\w*|повестк\w*|исполнительн\w*|частн\w+\s+судебн\w+\s+исполнител\w*|чси", "Похоже на документ суда или судебного исполнителя"),
    ("bank_debt", r"задолженност\w*|просрочк\w*|кредит\w*|коллектор\w*", "Похоже на письмо о задолженности"),
    ("receipt", r"чек\b|кассов\w+\s+чек|итого|фискальн\w*", "Похоже на кассовый чек"),
)
DOC_TEMPLATES = {
    "fine": ("Оплатить штраф или обжаловать его, если не согласны", "Проверьте штраф на egov.kz; оплачивайте только по реквизитам из постановления", "Обычно начисляются дополнительные меры взыскания — точные последствия смотрите в самом постановлении"),
    "tax": ("Уплатить налог или подать сведения", "egov.kz, приложение банка или налоговый орган по месту жительства", "Могут начислить пеню; уточните в налоговом органе"),
    "utility": ("Оплатить услуги за указанный период", "Приложение банка, касса поставщика; спорные суммы — заявление поставщику на перерасчёт", "Может начисляться пеня, а при долгой неоплате — ограничение услуги в установленном порядке"),
    "subscription": ("Платить ежемесячно за подписку, если вы её не отключите", "Отключите подписку в приложении или личном кабинете сервиса, при списаниях без согласия — обратитесь в банк", "Списания продолжатся"),
    "court": ("Явиться, ответить или исполнить решение — смотрите текст документа", "Сверьте документ на egov.kz или в суде; при сомнениях — к юристу", "Решение могут принять без вас; срочно проконсультируйтесь с юристом"),
    "bank_debt": ("Погасить задолженность или связаться с банком", "Позвоните в банк по официальному номеру, а не по номеру из письма", "Может расти долг; проверьте, реален ли он, в приложении банка"),
    "receipt": ("Ничего — это подтверждение оплаты", "Сохраните чек: он нужен для возврата или гарантии", "Без чека сложнее вернуть товар"),
}


def mock_chek(ctx: dict) -> VerdictCard:
    text: str = ctx.get("text", "")
    hits: list[SignalHit] = ctx.get("signals", [])
    links: list[LinkReport] = ctx.get("links", [])
    sources: list[Retrieved] = ctx.get("sources", [])

    if ctx.get("has_attachments") and len(text.strip()) < 10:
        return VerdictCard(
            status="unknown", title="В демо-режиме я не читаю фото и PDF", kind="document",
            reasons=[Reason(text="Распознавание фото работает с подключённым ИИ (LLM_API_KEY в .env). Без него — только текст.")],
            confidence=0, do=["Перепишите текст документа сообщением", "Или вставьте ключ API в .env и перезапустите сервер"],
        )

    doc_type = next(((key, title) for key, pattern, title in DOC_TYPES if re.search(pattern, text, re.IGNORECASE)), None)
    money = find_money(text)
    deadline, _ = find_deadline(text, date.today())
    fake_payment = re.search(r"(перевед|оплат)\w*\s+.{0,30}(на\s+карт\w*|по\s+номеру\s+телефона|kaspi\s+gold)", text, re.IGNORECASE)
    bad_links = [link for link in links if link.risk >= 2]

    if doc_type is None:
        return VerdictCard(
            status="unknown", title="Не понял, что это за документ", kind="document",
            reasons=[Reason(text="В тексте нет признаков квитанции, штрафа, налога или письма госоргана.")],
            confidence=20, do=["Пришлите текст документа полностью (без личных данных)"],
        )

    key, title = doc_type
    want, where, ignore = DOC_TEMPLATES[key]
    reasons = [Reason(text=f"В тексте есть признаки: {title.lower().replace('похоже на ', '')}.")]
    if money:
        reasons.append(Reason(text="Указана сумма к оплате.", quote=money[0]))
    status = "yellow" if key != "receipt" else "green"
    dont = ["Не платите по ссылкам из SMS — только через официальные каналы"]
    if fake_payment or bad_links or risk_score(hits) >= 3:
        status = "red"
        title = "Похоже на поддельное требование оплаты"
        if fake_payment:
            reasons.insert(0, Reason(text="Оплату просят перевести на карту или по номеру телефона — госорганы так не делают.", quote=fake_payment.group(0)))
        for link in bad_links[:1]:
            reasons.insert(0, Reason(text=" ".join(link.findings[:1]), quote=link.url))
        dont = ["Не платите по этим реквизитам", "Не переходите по ссылке"]
    return VerdictCard(
        status=status, title=title, kind="document", reasons=reasons[:3],
        sources=[_source_ref(r) for r in sources[:2]], confidence=55,
        document=DocumentSection(what_they_want=want, amount=money[0] if money else "", deadline=deadline, where_to_go=where, if_ignore=ignore),
        do=[where], dont=dont,
    )


RIGHTS_TITLES = {
    "demo_guide_consumer_return": "Похоже, вы можете требовать возврата, обмена или ремонта товара",
    "demo_guide_employer_salary": "Работодатель обязан платить вовремя — есть куда обратиться",
    "demo_guide_rent": "Решение зависит от договора аренды — вот как действовать",
    "demo_guide_taxi": "Можно требовать возврата денег через сервис и претензию",
}


# Академические права в МУИТ: (заголовок карточки, кому адресовать заявление)
ACADEMIC_RIGHTS = {
    "iitu_appeal": ("Ты можешь подать апелляцию на оценку", "В Апелляционную комиссию МУИТ (через Офис-регистратор)"),
    "iitu_assessment": ("Есть официальный порядок: FX, пересдача, апелляция", "В Офис-регистратор МУИТ"),
    "iitu_summer_semester": ("Задолженность можно закрыть в летнем семестре", "Директору Департамента по академическим вопросам МУИТ"),
    "iitu_academic_leave": ("Ты можешь оформить академический отпуск", "Ректору МУИТ (через деканат факультета)"),
    "iitu_transfer": ("Перевод и восстановление — по правилам университета", "Ректору МУИТ"),
    "iitu_trust_box": ("О нарушении можно сообщить конфиденциально", "В ящик доверия МУИТ (trust@iitu.edu.kz)"),
    "iitu_honesty_code": ("Кодекс академической честности защищает тебя", "В деканат факультета"),
    "iitu_dormitory": ("Правила общежития — вот что стоит знать", "Администрации общежития"),
    "iitu_psych_support": ("Тебе могут помочь бесплатно и конфиденциально", "В психологическую службу МУИТ"),
}


def mock_prava(ctx: dict) -> VerdictCard:
    text: str = ctx.get("text", "")
    sources: list[Retrieved] = ctx.get("sources", [])
    guide = next((r for r in sources if r.source.id in RIGHTS_TITLES or r.source.id in ACADEMIC_RIGHTS), None)
    if guide is None:
        return VerdictCard(
            status="unknown", title="Не нашёл источника по вашей ситуации", kind="rights",
            reasons=[Reason(text="В базе источников нет материала по этой теме — не буду гадать о ваших правах.")],
            confidence=20, do=["Опишите ситуацию подробнее: кто, что, когда, сколько", "Проконсультируйтесь с юристом или в ЦОН"],
        )
    s = guide.source
    summary = text.strip().replace("\n", " ")[:300]
    if s.id in ACADEMIC_RIGHTS:
        title, addressee = ACADEMIC_RIGHTS[s.id]
        letter = (
            f"{addressee}\n"
            "от студента [ФИО], группа [группа], образовательная программа [ОП], [курс] курс\n\n"
            "ЗАЯВЛЕНИЕ\n\n"
            f"Прошу [ваша просьба]. Обстоятельства: {summary}\n\n"
            "Основание: [документ или правило МУИТ, на которое вы опираетесь].\n"
            "Приложения: [скриншоты, работа, переписка — по необходимости].\n\n"
            "[дата], [подпись]"
        )
        first = next((x for x in _sentences(s.text) if not x.startswith("⚠️")), "")
        return VerdictCard(
            status="yellow", title=title, kind="rights",
            reasons=[Reason(text=first or "Источник описывает порядок действий."),
                     Reason(text="Сроки и форму заявления уточни в подразделении — бот не придумывает даты.")],
            sources=[_source_ref(guide)], confidence=55, do=s.do, dont=s.dont, claim_letter=letter,
        )
    letter = (
        "Кому: [наименование организации / ФИО], [адрес]\n"
        "От: [ваше ФИО], [телефон]\n\n"
        "ПРЕТЕНЗИЯ\n\n"
        f"[дата] произошла следующая ситуация: {summary}\n\n"
        "Считаю, что мои права нарушены. Прошу [ваше требование: вернуть деньги / заменить товар / выплатить задолженность] "
        "в срок до [дата]. Ответ прошу дать в письменной форме.\n\n"
        "В случае отказа оставляю за собой право обратиться в уполномоченный государственный орган и в суд.\n\n"
        "Приложения: [копии чека, договора, переписки].\n\n"
        "[дата], [подпись]"
    )
    first = next((x for x in _sentences(s.text) if not x.startswith("⚠️")), "")
    return VerdictCard(
        status="yellow", title=RIGHTS_TITLES[s.id], kind="rights",
        reasons=[Reason(text=first or "Источник описывает общий порядок действий."), Reason(text="Конкретные сроки и условия проверьте в законе на adilet.zan.kz.")],
        sources=[_source_ref(guide)], confidence=55, do=s.do, dont=s.dont, claim_letter=letter,
    )


B_MARKERS = re.compile(r"\b(он|она|они|сосед\w*|хозя\w*|продав\w*|начальник\w*|муж|жена|брат|сестр\w*)\b.{0,20}\b(говор\w*|счита\w*|утвержда\w*|сказал\w*|требу\w*|хочет|отказ\w*)|по\s+(его|её|ее|их)\s+словам|а\s+(он|она|они)\b", re.IGNORECASE)
EVIDENCE = re.compile(r"\d|чек|договор|расписк\w*|переписк\w*|скриншот\w*|фото|видео|перевод\w*", re.IGNORECASE)
CLAIM = re.compile(r"говор\w*|якобы|счита\w*|обещал\w*|кажется|утвержда\w*|сказал\w*", re.IGNORECASE)


def mock_spor(ctx: dict) -> VerdictCard:
    sentences = _sentences(ctx.get("text", ""))
    b = [s for s in sentences if B_MARKERS.search(s)]
    a = [s for s in sentences if s not in b]
    facts = [s for s in sentences if EVIDENCE.search(s) and not CLAIM.search(s)]
    claims = [s for s in sentences if CLAIM.search(s)]
    has_money = bool(find_money(ctx.get("text", "")))
    to_prove = []
    if has_money:
        to_prove.append("Подтвердить сумму и факт передачи денег (перевод, чек, расписка)")
    to_prove += ["Уточнить, о чём именно договаривались и когда", "Найти переписку или свидетелей договорённости"]
    dispute = DisputeSection(
        position_a=" ".join(a[:2]) or "Позиция автора в тексте не описана.",
        position_b=" ".join(b[:2]) or "Позиция второй стороны в тексте не описана — спросите её напрямую.",
        confirmed_facts=facts[:3],
        unconfirmed_claims=claims[:3],
        to_prove=to_prove[:3],
        options=["Встретиться и сверить факты по документам и переписке", "Пригласить нейтрального третьего человека",
                 "Записать итоговое решение письменно (/dogovorilis)"],
        neutral_message="Давай спокойно разберёмся. Я вижу ситуацию так: [ваша позиция одной фразой]. "
                        "Мне важно понять и твою сторону. Давай сверим, о чём мы договаривались, и найдём решение, которое устроит обоих.",
    )
    return VerdictCard(
        status="yellow", title="Разбор спора: факты отдельно, позиции отдельно", kind="dispute",
        reasons=[Reason(text="Я не решаю, кто прав, — я разделяю позиции, факты и то, что ещё нужно доказать."),
                 Reason(text=f"Подтверждённых фактов в тексте: {len(facts)}, утверждений без подтверждения: {len(claims)}.")],
        confidence=50, dispute=dispute,
        do=["Обсуждайте факты, а не личности", "Если речь о деньгах или законе — посмотрите режим /prava"],
        dont=["Не переходите на оскорбления — это ослабляет вашу позицию"],
    )


def mock_agreement(ctx: dict) -> AgreementDraft:
    text: str = ctx.get("text", "")
    today: date = ctx.get("today", date.today())
    money = find_money(text)
    deadline, deadline_iso = find_deadline(text, today)
    names = [m for m in re.findall(r"@\w+|\b[А-ЯЁ][а-яё]{2,}\b", text) if m.lower() not in ("договорились", "до", "мы", "если")]
    breach = re.search(r"(если\s+не\b.+|в\s+случае\s+.+|иначе\s+.+)", text, re.IGNORECASE)
    missing = []
    if not deadline:
        missing.append("Не указан срок")
    if not names:
        missing.append("Не указано, кто участвует")
    return AgreementDraft(
        who=", ".join(dict.fromkeys(names[:3])), what=text.strip()[:300], amount=money[0] if money else "",
        deadline=deadline, deadline_iso=deadline_iso, on_breach=breach.group(0)[:200] if breach else "", missing=missing,
    )


def mock_simplify(ctx: dict) -> SimpleExplanation:
    card: VerdictCard = ctx["card"]
    parts = {
        "red": "Это опасно.", "yellow": "Здесь нужно быть внимательным.",
        "green": "Тут всё в порядке.", "unknown": "Я пока не могу сказать точно.",
    }[card.status]
    text = f"{parts} {card.title}."
    if card.dont:
        text += f" Главное: {card.dont[0][0].lower() + card.dont[0][1:]}."
    if card.do:
        text += f" Что сделать: {card.do[0][0].lower() + card.do[0][1:]}."
    return SimpleExplanation(text=text.replace("..", "."))


RISKY = re.compile(r"\b\d{4,6}\b|код\w*\s+\d|\b(перевел\w*|переведу|отправил\w*|отправлю|скинул\w*|скину|установил\w*|установлю|диктую|вот\s+(код|номер|данные)|мой\s+иин|да,?\s+(конечно|хорошо)|согласен|согласна|оплачу|оплатил\w*|сейчас\s+сделаю)\b", re.IGNORECASE)
SAFE = re.compile(r"\b(нет|не\s+буду|не\s+стану|перезвоню|позвоню\s+(в\s+банк|сам|сама)|мошен\w*|развод\w*|не\s+сообщу|не\s+дам|не\s+скажу|проверю|заблок\w*|до\s+свидания|стоп|не\s+верю|полици\w*|без\s+договора\s+не)\b", re.IGNORECASE)


def mock_trainer_reply(ctx: dict) -> TrainerReply:
    scenario = ctx["scenario"]
    dialog: list[dict] = ctx["dialog"]
    bot_turns = sum(1 for m in dialog if m["role"] == "bot")
    user_msgs = [m["text"] for m in dialog if m["role"] == "user"]
    last = user_msgs[-1] if user_msgs else ""
    safe_count = sum(1 for m in user_msgs if SAFE.search(m))
    if RISKY.search(last) and not SAFE.search(last):
        return TrainerReply(message="Отлично, спасибо! Всё прошло успешно. (Учебная игра окончена — посмотрим разбор.)", finished=True)
    if safe_count >= 2:
        return TrainerReply(message="Ну как хотите… (собеседник пропал — учебная игра окончена, посмотрим разбор)", finished=True)
    index = bot_turns - 1  # первая реплика — opener, дальше идём по сценарию
    if index >= len(scenario.script):
        return TrainerReply(message="(Собеседник перестал отвечать. Учебная игра окончена — посмотрим разбор.)", finished=True)
    return TrainerReply(message=scenario.script[index], finished=False)


def mock_trainer_review(ctx: dict) -> TrainerReview:
    scenario = ctx["scenario"]
    user_msgs = [m["text"] for m in ctx["dialog"] if m["role"] == "user"]
    risky = [m for m in user_msgs if RISKY.search(m) and not SAFE.search(m)]
    safe = [m for m in user_msgs if SAFE.search(m)]
    immunity = 100 - 40 * len(risky) - (0 if safe else 20)
    if user_msgs and SAFE.search(user_msgs[0]):
        immunity = min(100, immunity + 10)
    immunity = max(0, min(100, immunity))
    good = [f"Вы ответили: «{m[:80]}» — правильная реакция." for m in safe[:2]]
    mistakes = [f"«{m[:80]}» — в реальной ситуации это могло стоить денег или данных." for m in risky[:2]]
    if not user_msgs:
        mistakes.append("Вы не ответили ни разу — попробуйте пройти сценарий полностью.")
    summary = (
        "Отлично! Вы распознали манипуляцию и не поддались." if immunity >= 80
        else "Неплохо, но в паре мест вы рисковали." if immunity >= 50
        else "В этот раз «мошенник» добился своего. Ничего страшного — для этого и нужен тренажёр."
    )
    return TrainerReview(
        immunity=immunity, summary=summary, red_flags=list(scenario.red_flags), good_moves=good, mistakes=mistakes,
        tips=["Сами перезванивайте в организацию по официальному номеру", "Никому не сообщайте коды из SMS",
              "Если торопят и пугают — это повод остановиться"],
    )


# ---------------------------------------------------------------- университет (МУИТ)


def _best_sentences(source_text: str, query: str, limit: int = 2) -> str:
    """Самые подходящие к вопросу фразы источника (демо-ответ — выдержка, без пересказа)."""
    words = set(tokenize(query))
    sentences = [x for x in _sentences(source_text.replace("\n", " ")) if not x.startswith("⚠️")]
    scored = sorted(sentences, key=lambda x: -len(words & set(tokenize(x))))
    best = [x for x in scored[:limit] if words & set(tokenize(x))]
    return " ".join(x for x in sentences if x in best)[:600]  # порядок — как в источнике


QUESTION_WORDS = {stem(w) for w in ("какой", "какая", "какие", "каком", "сколько", "почему", "зачем", "нужно", "можно", "могу", "мне", "надо")}


def _coverage(question: str, source_tokens: list[str]) -> float:
    """Какая доля смысловых слов вопроса есть в источнике (без «какой», «сколько» и т. п.)."""
    words = set(tokenize(question)) - QUESTION_WORDS
    return len(words & set(source_tokens)) / len(words) if words else 0.0


def mock_vopros(ctx: dict) -> VerdictCard:
    text = ctx.get("text", "")
    sources: list[Retrieved] = ctx.get("sources", [])
    # Демо без ИИ: отвечаем выдержкой, только если источник покрывает бо́льшую часть вопроса.
    top = next((r for r in sources if r.score >= 4.0 and _coverage(text, r.source.tokens) >= 0.5), None)
    if top is None:
        return VerdictCard(
            status="unknown", kind="insufficient", title="Не знаю: в базе официальных источников нет ответа на этот вопрос",
            reasons=[Reason(text="Я отвечаю только по официальным страницам МУИТ, а подходящей среди них не нашёл.")], confidence=20,
        )
    s = top.source
    answer = _best_sentences(s.text, text) or next(iter(_sentences(s.text)), "")
    return VerdictCard(
        status="green" if top.score >= 8.0 else "yellow", kind="answer",
        title="Нашёл ответ: " + s.title.replace("МУИТ: ", "").split(" (")[0], answer=answer,
        reasons=[Reason(text=f"Ответ взят из источника «{s.title}» (проверено {s.checked or s.date}).")],
        sources=[_source_ref(top)], confidence=70 if top.score >= 8.0 else 55, do=s.do[:3], dont=s.dont[:2],
    )


MONTHS_RE = r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
WHEN_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b|\b\d{1,2}\s+" + MONTHS_RE + r"\b|"
                     r"\b(?:понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресенье)\b", re.IGNORECASE)
TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b|\b\d{1,2}\s*(?:час\w*|ч\.)", re.IGNORECASE)
RELATIVE_RE = re.compile(r"\b(?:завтра|послезавтра|сегодня|на следующей неделе|скоро|как обычно|в ближайшее время)\b", re.IGNORECASE)
WHERE_RE = re.compile(r"\bауд\w*|\bкаб\w*|корпус\w*|\bzoom\b|\bteams\b|\bmeet\b|ссылк\w*|онлайн|\blms\b|platonus|dl\.iitu|https?://\S+", re.IGNORECASE)
WHO_RE = re.compile(r"групп\w*|поток\w*|\b\d\s*курс\w*|всем студент\w*|\b[A-ZА-Я]{2,5}-\d{2,4}\b|старост\w*", re.IGNORECASE)
HOW_RE = re.compile(r"сда\w*|загруз\w*|принес\w*|взять|формат\w*|pdf|docx|отправ\w*", re.IGNORECASE)


def mock_obyavlenie(ctx: dict) -> VerdictCard:
    text = ctx.get("text", "").strip()
    when, time, relative = WHEN_RE.search(text), TIME_RE.search(text), RELATIVE_RE.search(text)
    where, who, how = WHERE_RE.search(text), WHO_RE.search(text), HOW_RE.search(text)
    reasons: list[Reason] = []
    missing: list[str] = []
    if not when:
        missing.append("дата")
        if relative:
            reasons.append(Reason(text="Относительная дата: через день сообщение прочитают по-разному. Укажите число и месяц.", quote=relative.group(0)))
        else:
            reasons.append(Reason(text="Не указана дата."))
    if not time:
        missing.append("время")
    if not where:
        missing.append("место или ссылка")
        reasons.append(Reason(text="Не указано, где: аудитория, корпус или ссылка на онлайн-встречу."))
    if not who:
        missing.append("для кого")
        reasons.append(Reason(text="Не указано, для какой группы или потока."))
    sentences = _sentences(text)
    first = sentences[0] if sentences else text
    calm = "\n".join([
        f"📌 Что: {first.rstrip('.')}",
        f"📅 Когда: {when.group(0) if when else '[дата]'}, {time.group(0) if time else '[время]'}",
        f"📍 Где: {where.group(0) if where else '[аудитория / ссылка]'}",
        f"👥 Для кого: {who.group(0) if who else '[группа / поток]'}",
        f"✅ Что сделать: {'см. выше' if how else '[что принести / как сдать]'}",
    ])
    status = "green" if not missing else ("red" if len(missing) >= 3 else "yellow")
    return VerdictCard(
        status=status, kind="announcement", title="Объявление понятно" if not missing else "Не хватает: " + ", ".join(missing),
        reasons=reasons[:3], confidence=60,
        rewrite=RewriteSection(calm_text=calm, how_it_sounds="Суть понятна, но студенты переспросят детали." if missing else "Сообщение однозначное."),
        do=["Заполните поля в квадратных скобках и закрепите сообщение в чате группы", "Продублируйте срок в Platonus или LMS"],
    )


def mock_news_summary(ctx: dict) -> SimpleExplanation:
    from backend.app.university.news import excerpt

    return SimpleExplanation(text=excerpt(ctx.get("lead", ""), ctx.get("title", "")))


def mock_syllabus(ctx: dict):
    from backend.app.services.campus import syllabus_rules

    return syllabus_rules(ctx.get("text", ""), ctx.get("today") or date.today())


def mock_steps(ctx: dict):
    from backend.app.cards.schema import PlanStep, StepPlan
    from backend.app.services.planner import steps_preview_rules

    steps = steps_preview_rules(ctx.get("title", ""), ctx.get("deadline", ""), ctx.get("today"))
    return StepPlan(steps=[PlanStep(**s) for s in steps])


def mock_resume(ctx: dict):
    from backend.app.services.campus import resume_rules

    return resume_rules(ctx.get("text", ""))


class MockLLMClient(LLMClient):
    name = "mock"
    model = "demo-rules"

    async def generate(self, request: LLMRequest) -> LLMResult:
        task = request.task
        ctx = request.context
        if task.startswith("check:"):
            mode = task.split(":", 1)[1]
            builder = {
                "pravda": mock_pravda, "razvod": mock_razvod, "chek": mock_chek,
                "prava": mock_prava, "spor": mock_spor,
                "vopros": mock_vopros, "obyavlenie": mock_obyavlenie,
            }[mode]
            card = builder(ctx)
            card.notes.append(DEMO_NOTE)
            return LLMResult(card, self.name, self.model)
        builders = {
            "agreement": mock_agreement, "simplify": mock_simplify,
            "trainer_reply": mock_trainer_reply, "trainer_review": mock_trainer_review,
            "news_summary": mock_news_summary,
            "syllabus": mock_syllabus, "steps": mock_steps, "resume": mock_resume,
        }
        return LLMResult(builders[task](ctx), self.name, self.model)
