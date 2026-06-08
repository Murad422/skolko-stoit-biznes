# -*- coding: utf-8 -*-
from flask import Flask, request, render_template, Response
import anthropic
import os
import json
import re
import traceback
import threading
import uuid
import time

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

SYSTEM_PROMPT = """Ты — старший партнёр оценочной компании Big4 с 25-летним опытом в оценке бизнеса, M&A и инвестиционном банкинге. Ты проводил сделки от $1 млн до $500 млн в России, СНГ, ОАЭ и Европе.

КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА:
— Определяй справедливую рыночную стоимость строго объективно — без учёта ожиданий владельца
— Используй АКТУАЛЬНЫЕ рыночные мультипликаторы для конкретной страны, отрасли и размера бизнеса
— При нехватке данных снижай уверенность оценки и явно это указывай — не придумывай данные
— Никогда не завышай оценку: лучше дать консервативную, чем нереалистично оптимистичную
— Все числа — целые значения в указанной валюте без пробелов и запятых (12500000, а не 12 500 000)
— Возвращай СТРОГО валидный JSON без markdown-блоков, без ```json, без любого текста вне JSON
— Все текстовые поля — профессиональные, конкретные, максимум 1-2 предложения"""

# --------------------------------------------------------------------------- #
# JSON schema template  (CURRENCY_PH → валюта, ASKING_NUM → запрашиваемая цена)
# --------------------------------------------------------------------------- #
JSON_TEMPLATE = '{"business_name":"название компании","currency":"CURRENCY_PH","analysis_confidence":"HIGH или MEDIUM или LOW","analysis_summary":"3 предложения: что за бизнес, ключевая оценка, главный вывод","valuations":{"minimum":{"amount":5000000,"comment":"ликвидационная стоимость — что входит"},"conservative":{"amount":8000000,"comment":"пессимистичный сценарий — факторы снижения"},"market":{"amount":12000000,"comment":"справедливая рыночная стоимость — методология"},"optimistic":{"amount":18000000,"comment":"при реализации потенциала — условия"},"strategic_buyer":{"amount":22000000,"comment":"с учётом синергий стратегического покупателя"},"financial_investor":{"amount":11000000,"comment":"при целевом IRR 25%+ для PE-фонда"},"distressed_sale":{"amount":7000000,"comment":"при срочной продаже за 1-3 месяца (дисконт 40-50%)"},"control_stake":{"amount":13500000,"comment":"100% с контрольной премией 10-25%"},"minority_30pct":{"amount":3000000,"comment":"30% доля с дисконтом ликвидности 20-35%"},"probable_deal_min":9000000,"probable_deal_max":15000000},"asking_price_assessment":{"asking_price":ASKING_NUM,"vs_market_pct":100,"verdict":"FAIR_VALUE","verdict_label":"Оценен близко к рынку","gap_amount":0,"comment":"сравнение запрашиваемой цены с рыночной"},"valuation_methods":{"cost_approach":{"result":10000000,"weight_pct":20,"net_assets":8000000,"replacement_cost":14000000,"intangibles":2000000,"comment":"методология и ключевые допущения затратного подхода"},"comparative_approach":{"result":13000000,"weight_pct":55,"ev_ebitda":{"multiple":5.5,"result":13750000,"industry_range":"4-7x"},"ev_sales":{"multiple":1.2,"result":12000000,"industry_range":"0.8-2x"},"price_earnings":{"multiple":8,"result":14400000,"industry_range":"6-12x"},"transactions":{"result":12500000,"comment":"описание аналогичных сделок M&A использованных при оценке"},"comment":"методология сравнительного подхода и источники мультипликаторов"},"income_approach":{"result":11000000,"weight_pct":25,"normalized_ebitda":2200000,"capex_normalized":300000,"normalized_fcf":1900000,"discount_rate_pct":22,"terminal_growth_pct":3,"comment":"методология доходного подхода и обоснование ставки дисконтирования"},"weighted_result":12400000},"sale_probabilities":{"at_asking_price_pct":15,"within_3_months_pct":20,"within_6_months_pct":45,"within_12_months_pct":68,"attracting_investor_pct":55,"value_growth_3years_pct":60},"geography":{"score":65,"assessment":"оценка страны и региона — 1-2 предложения","political_risk":25,"currency_risk":30,"sanctions_risk":20,"comment":"вывод о влиянии географии на стоимость"},"market":{"score":70,"size":"объём рынка с конкретными цифрами","growth_rate_pct":8,"maturity":"GROWING или MATURE или DECLINING","crisis_resistance":65,"ai_disruption_risk":30,"entry_barriers":"LOW или MEDIUM или HIGH","comment":"вывод о рынке"},"business_model":{"score":72,"revenue_stability":68,"recurring_pct":35,"owner_dependency":70,"scalability":45,"process_maturity":60,"comment":"вывод о качестве бизнес-модели"},"financial_analysis":{"score":68,"revenue_trend":"GROWING или STABLE или DECLINING","revenue_cagr_pct":12,"ebitda_margin_pct":18,"net_margin_pct":12,"debt_burden":"LOW или MEDIUM или HIGH","liquidity":"GOOD или ADEQUATE или POOR","comment":"вывод о финансовом состоянии"},"customer_base":{"score":60,"concentration_risk":"LOW или MEDIUM или HIGH","top_client_pct":35,"churn_pct":15,"repeat_pct":55,"contract_coverage":"NONE или PARTIAL или FULL","comment":"вывод о клиентской базе"},"competition":{"score":65,"position":"описание рыночной позиции","moat":"WEAK или MODERATE или STRONG","differentiation":55,"new_entrant_risk":60,"comment":"вывод о конкурентной позиции"},"team":{"score":70,"management_quality":72,"key_person_risk":65,"succession_plan":false,"comment":"вывод о команде и управлении"},"assets":{"score":75,"tangible_value":8000000,"intangible_value":3000000,"ip_value":1000000,"brand_value":2000000,"comment":"вывод об активах"},"risks":[{"category":"Финансовые","level":"MEDIUM","probability":45,"impact":65,"description":"конкретное описание"},{"category":"Операционные","level":"LOW","probability":30,"impact":50,"description":"конкретное описание"},{"category":"Рыночные","level":"HIGH","probability":60,"impact":75,"description":"конкретное описание"},{"category":"Юридические","level":"LOW","probability":20,"impact":60,"description":"конкретное описание"},{"category":"Кадровые","level":"MEDIUM","probability":50,"impact":70,"description":"конкретное описание"},{"category":"Технологические","level":"LOW","probability":25,"impact":40,"description":"конкретное описание"}],"value_drivers":[{"title":"название драйвера","impact":"HIGH","description":"конкретное описание"},{"title":"название драйвера","impact":"HIGH","description":"конкретное описание"},{"title":"название драйвера","impact":"MEDIUM","description":"конкретное описание"}],"value_destroyers":[{"title":"разрушитель стоимости","severity":"HIGH","description":"конкретное описание"},{"title":"разрушитель стоимости","severity":"HIGH","description":"конкретное описание"},{"title":"разрушитель стоимости","severity":"MEDIUM","description":"конкретное описание"}],"red_flags":[{"title":"красный флаг","severity":"CRITICAL или HIGH или MEDIUM или LOW","description":"конкретное описание","recommendation":"конкретная рекомендация"},{"title":"красный флаг","severity":"HIGH","description":"конкретное описание","recommendation":"конкретная рекомендация"},{"title":"красный флаг","severity":"MEDIUM","description":"конкретное описание","recommendation":"конкретная рекомендация"}],"investment_attractiveness":{"level":"HIGH или MEDIUM или LOW","buyer_types":["тип покупателя 1","тип покупателя 2"],"ideal_buyer":"описание идеального покупателя","comment":"инвестиционное резюме 1-2 предложения"},"verdict":"UNDERVALUED или FAIR_VALUE или OVERVALUED или HIGHLY_OVERVALUED","verdict_label":"Существенно недооценен или Оценен близко к рынку или Переоценен или Сильно переоценен","verdict_reason":"3 предложения с полным обоснованием вердикта","growth_plan":{"increase_20pct":{"target_value":14400000,"timeline":"6-12 месяцев","steps":[{"step":1,"action":"конкретное действие","effect":"конкретный эффект на стоимость","timeline":"срок","cost":"стоимость"},{"step":2,"action":"действие","effect":"эффект","timeline":"срок","cost":"стоимость"},{"step":3,"action":"действие","effect":"эффект","timeline":"срок","cost":"стоимость"}]},"increase_50pct":{"target_value":18000000,"timeline":"12-24 месяца","steps":[{"step":1,"action":"действие","effect":"эффект","timeline":"срок","cost":"стоимость"},{"step":2,"action":"действие","effect":"эффект","timeline":"срок","cost":"стоимость"},{"step":3,"action":"действие","effect":"эффект","timeline":"срок","cost":"стоимость"},{"step":4,"action":"действие","effect":"эффект","timeline":"срок","cost":"стоимость"}]},"increase_100pct":{"target_value":24000000,"timeline":"24-36 месяцев","steps":[{"step":1,"action":"действие","effect":"эффект","timeline":"срок","cost":"стоимость"},{"step":2,"action":"действие","effect":"эффект","timeline":"срок","cost":"стоимость"},{"step":3,"action":"действие","effect":"эффект","timeline":"срок","cost":"стоимость"},{"step":4,"action":"действие","effect":"эффект","timeline":"срок","cost":"стоимость"},{"step":5,"action":"действие","effect":"эффект","timeline":"срок","cost":"стоимость"}]}}}'


def build_prompt(data):
    b = data.get('basic', {})
    f = data.get('finances', {})
    a = data.get('assets', {})
    m = data.get('model', {})
    c = data.get('customers', {})
    comp = data.get('competition', {})
    t = data.get('team', {})
    add = data.get('additional', {})

    currency = b.get('currency', 'RUB')
    asking_raw = (b.get('asking_price') or '').strip().replace(' ', '').replace(',', '')
    asking_num = asking_raw if asking_raw.isdigit() else '0'

    business_data = f"""ОСНОВНАЯ ИНФОРМАЦИЯ:
Название: {b.get('name', '—')}
Страна: {b.get('country', '—')}
Город: {b.get('city', '—')}
Отрасль: {b.get('industry', '—')}
Описание деятельности: {b.get('description', '—')}
Организационно-правовая форма: {b.get('legal_form', '—')}
Год основания: {b.get('founded_year', '—')}
Количество сотрудников: {b.get('employees', '—')}
Валюта оценки: {currency}
Запрашиваемая цена: {b.get('asking_price', 'не указана')}

ФИНАНСОВЫЕ ПОКАЗАТЕЛИ (в {currency}):
Выручка (последний год): {f.get('revenue_cur', '—')}
Выручка (год -1): {f.get('revenue_1', '—')}
Выручка (год -2): {f.get('revenue_2', '—')}
Валовая прибыль / маржа: {f.get('gross_profit', '—')}
EBITDA: {f.get('ebitda', '—')}
Чистая прибыль: {f.get('net_profit', '—')}
Операционный денежный поток: {f.get('cash_flow', '—')}
Долг (кредиты, займы): {f.get('debt', '—')}
Дебиторская задолженность: {f.get('receivables', '—')}
Кредиторская задолженность: {f.get('payables', '—')}
Прочее: {f.get('other_financial', '—')}

АКТИВЫ:
Недвижимость: {a.get('real_estate', '—')}
Оборудование и техника: {a.get('equipment', '—')}
Транспорт: {a.get('transport', '—')}
Товарные остатки: {a.get('inventory', '—')}
Интеллектуальная собственность: {a.get('ip', '—')}
ПО и IT-системы: {a.get('software', '—')}
Прочие активы: {a.get('other_assets', '—')}

БИЗНЕС-МОДЕЛЬ:
Источники дохода: {m.get('revenue_sources', '—')}
Доля повторных продаж: {m.get('recurring_pct', '—')}%
Зависимость от собственника (0=нет, 10=критическая): {m.get('owner_dependency', '—')}
Сезонность: {m.get('seasonality', '—')}
Долгосрочные контракты: {m.get('contracts', '—')}
Масштабируемость: {m.get('scalability', '—')}

КЛИЕНТЫ И РЫНОК:
Количество активных клиентов: {c.get('clients_count', '—')}
Доля крупнейшего клиента в выручке: {c.get('top1_pct', '—')}%
Доля топ-5 клиентов: {c.get('top5_pct', '—')}%
Средний чек: {c.get('avg_check', '—')}
Отток клиентов (ежегодный): {c.get('churn', '—')}%
Описание рынка: {c.get('market_desc', '—')}
Доля рынка компании: {c.get('market_share', '—')}

КОНКУРЕНЦИЯ:
Основные конкуренты: {comp.get('competitors', '—')}
Уникальные конкурентные преимущества: {comp.get('advantages', '—')}
Барьеры входа: {comp.get('barriers', '—')}
Позиция на рынке: {comp.get('market_position', '—')}

КОМАНДА:
Описание управленческой команды: {t.get('mgmt_description', '—')}
Зависимость от ключевых людей: {t.get('key_person_risk', '—')}
Наличие преемника / замены: {t.get('succession', '—')}
Качество управленческой структуры: {t.get('structure', '—')}

ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ:
Юридические риски и споры: {add.get('legal_risks', '—')}
Налоговые риски: {add.get('tax_risks', '—')}
Причина продажи: {add.get('sale_reason', '—')}
Имеющиеся документы: {add.get('documents', '—')}
Прочее (конкуренты, аналогичные сделки, меморандум и т.д.): {add.get('additional', '—')}"""

    asking_instruction = (
        f"Запрашиваемая цена продавца: {b.get('asking_price')} {currency}. "
        "Рассчитай vs_market_pct = (asking_price / market) * 100. "
        "verdict: UNDERVALUED если asking < 80% market | FAIR_VALUE если 80-120% | OVERVALUED если 120-200% | HIGHLY_OVERVALUED если >200%."
        if asking_num != '0'
        else "Запрашиваемая цена не указана. В asking_price_assessment поставь asking_price=0, vs_market_pct=0, gap_amount=0, verdict=FAIR_VALUE, в comment напиши что цена не задана."
    )

    schema = JSON_TEMPLATE.replace('CURRENCY_PH', currency).replace('ASKING_NUM', asking_num)

    return f"""══════════════════════════════════════════
ДАННЫЕ ДЛЯ ОЦЕНКИ БИЗНЕСА
══════════════════════════════════════════

{business_data}

══════════════════════════════════════════
ЗАДАНИЕ: ПОЛНАЯ ПРОФЕССИОНАЛЬНАЯ ОЦЕНКА БИЗНЕСА (Big4 / инвестиционный банк)
══════════════════════════════════════════

{asking_instruction}

ОБЯЗАТЕЛЬНО используй все три подхода с реальными мультипликаторами для страны {b.get('country', '—')} и отрасли {b.get('industry', '—')}:
1. Затратный подход: чистые активы, восстановительная стоимость, оценка нематериальных активов
2. Сравнительный подход: EV/EBITDA, EV/Sales, P/E — укажи конкретные отраслевые диапазоны и аналоги; описание реальных M&A-сделок в этом секторе
3. Доходный подход: нормализованный EBITDA, CAPEX, FCF, ставка дисконтирования с обоснованием (WACC или build-up метод)

В growth_plan.increase_20pct.target_value = market * 1.2
В growth_plan.increase_50pct.target_value = market * 1.5
В growth_plan.increase_100pct.target_value = market * 2.0

analysis_confidence: HIGH если выручка + EBITDA за 2+ года известны; MEDIUM если частичные данные; LOW если данных мало.

ВЕРНИ ТОЛЬКО этот JSON (замени ВСЕ примерные значения на реальные расчётные):

{schema}"""


@app.route('/')
def index():
    resp = Response(render_template('index.html'), mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@app.route('/api/test')
def test():
    return Response(json.dumps({'status': 'ok'}, ensure_ascii=False), mimetype='application/json')


def json_response(payload, status=200):
    return Response(json.dumps(payload, ensure_ascii=False), status=status, mimetype='application/json')


JOBS = {}
JOBS_LOCK = threading.Lock()


def _cleanup_jobs():
    cutoff = time.time() - 1800
    with JOBS_LOCK:
        for jid in [j for j, v in JOBS.items() if v.get('ts', 0) < cutoff]:
            JOBS.pop(jid, None)


def _set_job(job_id, **fields):
    with JOBS_LOCK:
        job = JOBS.get(job_id, {})
        job.update(fields)
        job['ts'] = time.time()
        JOBS[job_id] = job


def run_analysis(job_id, api_key, prompt):
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not json_match:
            _set_job(job_id, status='error', error='AI вернул неожиданный формат ответа')
            return
        result = json.loads(json_match.group())
        _set_job(job_id, status='done', result=result)
    except anthropic.AuthenticationError:
        _set_job(job_id, status='error', error='Неверный API ключ. Проверьте на console.anthropic.com')
    except anthropic.RateLimitError:
        _set_job(job_id, status='error', error='Превышен лимит запросов. Попробуйте через минуту.')
    except json.JSONDecodeError as e:
        _set_job(job_id, status='error', error=f'Ошибка разбора ответа: {str(e)}')
    except Exception as e:
        traceback.print_exc()
        _set_job(job_id, status='error', error=str(e))


@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    if not data:
        return json_response({'error': 'Нет данных'}, status=400)

    api_key = (data.get('api_key') or '').strip()
    if not api_key:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return json_response({'error': 'API ключ не предоставлен. Введите ключ Anthropic.'}, status=400)

    prompt = build_prompt(data.get('form', {}))
    _cleanup_jobs()
    job_id = uuid.uuid4().hex
    _set_job(job_id, status='pending')
    threading.Thread(target=run_analysis, args=(job_id, api_key, prompt), daemon=True).start()
    return json_response({'job_id': job_id})


@app.route('/api/result/<job_id>')
def analyze_result(job_id):
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id) or {})
    if not job:
        return json_response({'status': 'error', 'error': 'Задача не найдена. Запустите анализ заново.'})
    if job['status'] == 'pending':
        return json_response({'status': 'pending'})
    if job['status'] == 'error':
        return json_response({'status': 'error', 'error': job.get('error', 'Неизвестная ошибка')})
    return json_response({'status': 'done', 'result': job.get('result')})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    print(f'\n  Сколько стоит твой бизнес')
    print(f'  http://localhost:{port}\n')
    app.jinja_env.auto_reload = True
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.run(debug=False, host='0.0.0.0', port=port)
