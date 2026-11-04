"""Интерфейс «Список покупок по блюду».  Запуск:  streamlit run streamlit_app.py

UI только собирает ввод и показывает вывод. Проверки, вызов GigaChat и ошибки — в llm.py.
"""
import streamlit as st

from llm import FORMAT_OPTIONS, MAX_DISH_LENGTH, PEOPLE_OPTIONS, generate

st.set_page_config(page_title="Список покупок по блюду", page_icon="🛒")
st.title("🛒 Список покупок по блюду")
st.caption("Напишите, что хотите приготовить, — GigaChat составит список продуктов.")

# Состояние между нажатиями: последнее сообщение и последний успешный результат.
st.session_state.setdefault("message", None)      # Result последнего нажатия
st.session_state.setdefault("last_ok", None)      # Result последнего успешного ответа

# st.form: страница не перезапускается на каждый ввод символа, запрос уходит только по кнопке.
with st.form("shopping_form"):
    dish = st.text_area(
        "Что приготовить",
        placeholder="Например: борщ с говядиной",
        height=100,
        help=f"До {MAX_DISH_LENGTH} символов.",
    )
    col_people, col_format = st.columns(2)
    people = col_people.selectbox("На сколько человек", PEOPLE_OPTIONS, index=1)
    fmt = col_format.selectbox("Формат", FORMAT_OPTIONS)
    submitted = st.form_submit_button("Сгенерировать список покупок", type="primary")

if submitted:
    with st.spinner("Составляем список покупок…"):
        result = generate(dish, people, fmt)
    st.session_state.message = result
    if result.ok:
        st.session_state.last_ok = result

message = st.session_state.message
last_ok = st.session_state.last_ok

# Ошибка ввода — предупреждение, ошибка API — ошибка. В обоих случаях одно предложение, без traceback.
if message is not None and not message.ok:
    if message.kind == "validation":
        st.warning(message.text)
    else:
        st.error(message.text)

# Последний успешный результат не пропадает из-за ошибки при следующем нажатии.
if last_ok is not None:
    if message is not None and not message.ok:
        st.caption("Предыдущий результат:")
    st.subheader("Список покупок")
    st.markdown(last_ok.text)
    st.caption(f"Готово за {last_ok.elapsed:.1f} с")
