# -*- coding: utf-8 -*-
"""ИИ-агент ATS v2.

L1 (работает сейчас): сценарный диалог по дереву (scenario JSON в шаблоне). Канал — через
провайдера (SimChannel) или, в проде, через media-слой/ASR-ключевые слова.
L2 (LLM): интерфейс AgentLLM + заготовка OpenAI-совместимого клиента (включается настройкой
settings.llm.enabled и env ATS_LLM_KEY; реально используется после выбора ASR/TTS-стека, T30).
"""
import json
import re

from . import db


def normalize_answer(s):
    s = str(s or "").strip().lower()
    if s in ("1", "да", "yes", "y", "ага", "конечно", "хочу", "интересует"):
        return "1"
    if s in ("2", "нет", "no", "n", "не", "не надо", "не интересует"):
        return "2"
    return s


def load_scenario(template_id):
    t = db.fetch1("SELECT scenario FROM templates WHERE id=?", (template_id,))
    if not t:
        return {}
    try:
        return json.loads(t["scenario"] or "{}")
    except Exception:
        return {}


def default_scenario():
    return dict(db.DEFAULT_SCENARIO)


def run_scripted(channel, template_id, contact=None):
    """Прогон сценарного диалога. Возвращает dict: qualified, answers, summary, transcript."""
    scenario = load_scenario(template_id) or default_scenario()
    answers = []
    transcript = []
    contact = contact or {}

    def say(t):
        transcript.append(("агент", t))
        channel.say(t)

    def ask(q_text):
        a = channel.ask(q_text)
        transcript.append(("абонент", a))
        return a

    name = (contact.get("name") or "").strip()
    greeting = str(scenario.get("greeting", "")).replace("{name}", name)
    say(greeting or "Здравствуйте!")
    if str(scenario.get("intro", "")).strip():
        say(str(scenario["intro"]).replace("{name}", name))

    qualified = scenario.get("default_qualified", False)
    questions = scenario.get("questions") or []
    for q in questions:
        qid = q.get("id")
        text = str(q.get("text", "")).replace("{name}", name)
        if not text:
            continue
        ans = ask(text)
        norm = normalize_answer(ans)
        choice = None
        for key, c in (q.get("choices") or {}).items():
            if normalize_answer(key) == norm or norm in (key,):
                choice = c
                break
        if choice is None:
            choice = next(iter((q.get("choices") or {}).values()), {"next": "end", "qualified": False})
        answers.append({"q": qid, "question": text, "answer_raw": ans, "answer": norm})
        if "qualified" in choice and choice["qualified"] is not None:
            qualified = bool(choice["qualified"])
        if choice.get("next") == "end":
            break

    if qualified:
        say(str(scenario.get("qualified_text", "Спасибо, передаю ваш звонок специалисту.")))
    else:
        say(str(scenario.get("not_qualified_text", "Спасибо, до свидания.")))
    transcript.append(("агент", scenario.get("goodbye", "")))
    return {
        "qualified": qualified,
        "answers": answers,
        "summary": "; ".join("{}: {}".format(a["q"], a["answer_raw"]) for a in answers),
        "transcript": " | ".join("{}: {}".format(k, v) for k, v in transcript),
    }


# ---------------- L2: LLM-агент (каркас) ----------------
class AgentLLM:
    """OpenAI-совместимый чат-клиент (подойдёт для YandexGPT API, GigaChat через прокси и т.п.).
    Включается настройкой settings.llm; ключ — из env (settings.llm.api_key_env)."""
    def __init__(self, settings):
        llm = settings.get("llm", {}) or {}
        self.enabled = bool(llm.get("enabled"))
        self.base_url = llm.get("base_url", "").rstrip("/")
        self.model = llm.get("model", "")
        import os
        self.api_key = os.environ.get(llm.get("api_key_env", "ATS_LLM_KEY"), "")

    def available(self):
        return self.enabled and bool(self.base_url) and bool(self.api_key)

    def chat(self, messages, timeout=30):
        import urllib.request
        body = json.dumps({"model": self.model, "messages": messages}).encode("utf-8")
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + self.api_key})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

    def build_prompt(self, contact, criteria):
        return [{"role": "system",
                 "content": "Ты — ИИ-агент исходящего обзвона. Задай клиенту вопросы по критериям: "
                            + (criteria or "интересует ли предложение") +
                            ". В конце верни JSON: {\"qualified\": true/false, \"summary\": \"...\"}."},
                {"role": "user", "content": "Клиент: {} {}".format(contact.get("name", ""), contact.get("phone", ""))}]

    def parse_result(self, content):
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            return {"qualified": False, "summary": content[:200]}
        try:
            return json.loads(m.group(0))
        except Exception:
            return {"qualified": False, "summary": content[:200]}
