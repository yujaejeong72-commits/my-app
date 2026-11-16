"""나도 이제 고딩이에요! — 같은 숫자로 들어가는 가족 대화 앱.
app.py와 requirements.txt를 GitHub에 올리고 Streamlit Community Cloud에 배포하세요.
자녀/부모가 같은 숫자 10~16자리를 각자 입력하고 역할을 고르면 같은 방에 들어갑니다.
처음 입력한 사람이 방을 자동으로 만듭니다. 별도 가족 코드 발급, Google 로그인,
Secrets 설정은 필요하지 않습니다. 공유 숫자를 아는 사람은 어느 역할로든 입장할 수
있으니 민감한 내용을 입력하지 마세요. 서버의 SQLite 데이터는 재시작/재배포 시
소실될 수 있습니다. 기존 Google 로그인판 데이터와 자동 연결되지 않습니다.
"""
import html
import hashlib
import io
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import qrcode
import streamlit as st

DB_PATH = Path('family_shared.sqlite3')

@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=20000')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def setup():
    with db() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS families(
          id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS wishes(
          id TEXT PRIMARY KEY, family_id TEXT NOT NULL, title TEXT NOT NULL,
          reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          parent_reason TEXT, thanks TEXT, current_offer TEXT, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, wish_id TEXT NOT NULL,
          role TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
        ''')

def code_hash(code):
    return hashlib.sha256(('family-dialogue-v1:' + code).encode()).hexdigest()

def enter_family(code):
    if not (code.isascii() and code.isdecimal() and 10 <= len(code) <= 16):
        raise ValueError('서로 약속한 숫자 10~16자리를 입력해 주세요.')
    digest = code_hash(code)
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('INSERT OR IGNORE INTO families(id, code_hash, created) VALUES(?,?,?)',
                    (uuid.uuid4().hex, digest, time.time()))
        row = con.execute('SELECT id FROM families WHERE code_hash=?', (digest,)).fetchone()
    return row['id']

def check_family(con, fid, code):
    if not code or con.execute('SELECT 1 FROM families WHERE id=? AND code_hash=?',
                               (fid, code_hash(code))).fetchone() is None:
        raise ValueError('방에 다시 입장해 주세요.')

def add_event(con, wish_id, role, action, detail=''):
    con.execute('INSERT INTO events(wish_id,role,action,detail,created) VALUES(?,?,?,?,?)',
                (wish_id, role, action, detail, time.time()))

def new_wish(code, fid, title, reason):
    title, reason = title.strip(), reason.strip()
    if not (1 <= len(title) <= 70 and 1 <= len(reason) <= 700):
        raise ValueError('제목과 이유를 입력해 주세요 (각각 70자·700자 이내).')
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        check_family(con, fid, code)
        wid = uuid.uuid4().hex
        con.execute('INSERT INTO wishes(id,family_id,title,reason,created) VALUES(?,?,?,?,?)',
                    (wid, fid, title, reason, time.time()))
        add_event(con, wid, 'child', '요청', reason)

def move(code, fid, wid, role, action, text=''):
    text = text.strip()
    if len(text) > 700:
        raise ValueError('내용은 700자 이내로 입력해 주세요.')
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        check_family(con, fid, code)
        if role not in ('child', 'parent'): raise ValueError('올바른 역할을 선택해 주세요.')
        row = con.execute('SELECT * FROM wishes WHERE id=? AND family_id=?', (wid, fid)).fetchone()
        if row is None: raise ValueError('대화 카드를 찾을 수 없어요.')
        status, updates = row['status'], {}
        if role == 'parent' and status == 'pending' and action in ('approve', 'deny'):
            if not text: raise ValueError('허락 또는 거절의 이유를 적어 주세요.')
            updates = {'status': 'approved' if action == 'approve' else 'denied', 'parent_reason': text}
        elif role == 'child' and status == 'approved' and row['thanks'] is None and action == 'thanks':
            if not text: raise ValueError('감사의 마음을 적어 주세요.')
            updates = {'thanks': text}
        elif role == 'child' and status == 'denied' and action == 'offer':
            if not text: raise ValueError('교환 조건을 적어 주세요.')
            updates = {'status': 'offer_to_parent', 'current_offer': text}
        elif ((role == 'parent' and status == 'offer_to_parent') or
              (role == 'child' and status == 'offer_to_child')) and action in ('accept','reject','counter'):
            if action == 'counter':
                if not text: raise ValueError('역제안 내용을 적어 주세요.')
                updates = {'status': 'offer_to_child' if role == 'parent' else 'offer_to_parent', 'current_offer': text}
            else:
                updates = {'status': 'accepted' if action == 'accept' else 'rejected'}
        else:
            raise ValueError('현재 단계에서 가능한 동작이 아니에요. 새로고침 후 다시 확인해 주세요.')
        columns = ', '.join(f'{k}=?' for k in updates)
        con.execute(f'UPDATE wishes SET {columns} WHERE id=? AND family_id=?', (*updates.values(), wid, fid))
        add_event(con, wid, role, action, text)

def do(fn, *args):
    try:
        fn(*args)
        st.rerun()
    except (ValueError, sqlite3.Error) as exc:
        st.error(str(exc))

def esc(value):
    return html.escape(str(value or ''))

st.set_page_config(page_title='나도 이제 고딩이에요!', page_icon='🎒', layout='centered')
st.markdown('''<style>
.stApp, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {background:#fff !important;color:#111 !important}
html, body, p, label, span, h1, h2, h3, [data-testid="stMarkdownContainer"],
[data-testid="stMarkdownContainer"] *, [data-testid="stWidgetLabel"] *,
[data-testid="stCaptionContainer"], [data-testid="stAlert"] * {color:#111 !important}
.block-container{max-width:640px;padding:1rem 1rem 2rem}
.hero{background:#fff;border:3px solid #111;border-radius:19px;padding:18px 16px;margin:4px 0 17px;box-shadow:4px 5px 0 #111;color:#111}
.hero h1{font-size:1.65rem;line-height:1.3;margin:0;color:#111 !important;font-weight:850}
.hero p{font-size:1rem;line-height:1.5;color:#111 !important;margin:9px 0 0}
.card{background:#fff;border:2px solid #111;border-radius:14px;padding:15px;margin:12px 0}
.body{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.55;margin-top:9px}
.stButton button,div[data-testid="stFormSubmitButton"] button{background:#fff;color:#111 !important;border:2px solid #111;border-radius:11px;min-height:43px;font-weight:700}
.stButton button:hover,div[data-testid="stFormSubmitButton"] button:hover{background:#eee;color:#111 !important}
input,textarea,[data-baseweb="select"]>div{background:#fff !important;color:#111 !important;border-color:#555 !important}
</style>''', unsafe_allow_html=True)
st.markdown('<div class="hero"><h1>🎒 나도 이제 고딩이에요!</h1><p>마음을 전하고, 이유를 듣고, 함께 답을 찾아요.</p></div>', unsafe_allow_html=True)

try:
    setup()
except sqlite3.Error:
    st.error('저장 공간에 접근할 수 없어요. 배포 환경의 쓰기 권한을 확인해 주세요.')
    st.stop()

if not st.session_state.get('fid') or not st.session_state.get('shared_code'):
    st.write('자녀와 부모님이 **같은 숫자**를 입력하면 한 대화방으로 연결돼요.')
    with st.form('enter'):
        code_input = st.text_input('둘이 약속한 숫자 (10~16자리)', type='password', max_chars=16,
                                   help='처음 입력하면 방이 자동으로 만들어집니다. 숫자를 꼭 기억해 주세요.')
        role_label = st.radio('나는', ['🎒 자녀', '☕ 부모님'], horizontal=True)
        submitted = st.form_submit_button('같은 방으로 들어가기', use_container_width=True)
    if submitted:
        try:
            fid_new = enter_family(code_input)
            st.session_state.fid = fid_new
            st.session_state.shared_code = code_input
            st.session_state.role = 'child' if role_label == '🎒 자녀' else 'parent'
            st.rerun()
        except (ValueError, sqlite3.Error) as exc:
            st.error(str(exc))
    st.warning('이 숫자를 아는 사람은 자녀·부모 어느 역할로든 들어올 수 있어요. 민감한 내용은 적지 마세요. 서버 재시작·재배포 시 기록이 사라질 수 있습니다.')
    st.stop()

fid = st.session_state.fid
code = st.session_state.shared_code
role = st.session_state.role
with db() as con:
    check_family(con, fid, code)
with st.sidebar:
    st.write('현재 역할: '+('🎒 자녀' if role == 'child' else '☕ 부모님'))
    st.caption('둘이 약속한 같은 숫자로 각자 접속하세요. 다른 사람에게 알려주지 마세요.')
    if st.button('나가기', use_container_width=True):
        for key in ('fid', 'shared_code', 'role'):
            st.session_state.pop(key, None)
        st.rerun()
    with st.expander('접속 QR 만들기'):
        app_url = st.text_input('배포 앱 주소', placeholder='https://example.streamlit.app')
        if app_url.startswith('https://'):
            image = qrcode.make(app_url)
            output = io.BytesIO()
            image.save(output, format='PNG')
            st.image(output.getvalue(), width=170)
            st.download_button('QR 다운로드', output.getvalue(), 'app_qr.png', 'image/png')
        else:
            st.caption('QR에는 앱 주소만 들어갑니다. 공유 숫자는 따로 입력해야 해요.')
st.caption('🎒 자녀' if role == 'child' else '☕ 부모님')
if role == 'child':
    with st.expander('✍️ 내가 바라는 것 전하기'):
        with st.form('wish', clear_on_submit=True):
            title = st.text_input('바라는 것', max_chars=70)
            reason = st.text_area('왜 바라는지 / 걱정되는 부분', max_chars=700)
            sent = st.form_submit_button('📮 부모님께 전하기', use_container_width=True)
        if sent: do(new_wish, code, fid, title, reason)
with db() as con:
    check_family(con, fid, code)
    wishes = con.execute('SELECT * FROM wishes WHERE family_id=? ORDER BY created DESC LIMIT 50', (fid,)).fetchall()
labels = {'pending':'📮 부모님 답변 기다리는 중','approved':'🌼 허락','denied':'🌧️ 거절 · 딜 가능',
          'offer_to_parent':'🎁 부모님 응답 기다리는 중','offer_to_child':'🎁 자녀 응답 기다리는 중',
          'accepted':'🤝 교환 수락 · 종료','rejected':'🛑 교환 거부 · 종료'}
if not wishes:
    st.info('아직 대화가 없어요. 자녀가 먼저 바라는 것을 적으면 시작됩니다.')
    st.stop()
st.subheader('💌 대화 카드')
options = {w['id']: f"{w['title']} · {labels.get(w['status'],w['status'])}" for w in wishes}
selected = st.selectbox('카드 선택 (최근 50개)', list(options), format_func=lambda x: options[x])
w = next(w for w in wishes if w['id'] == selected)
st.markdown('<div class="card"><strong>💌 '+esc(w['title'])+'</strong><br>'+esc(labels.get(w['status'],w['status']))+
            '<div class="body">'+esc(w['reason'])+'</div></div>', unsafe_allow_html=True)
if w['parent_reason']:
    st.markdown('**☕ 부모님의 이유**')
    st.write(w['parent_reason'])
if w['current_offer']:
    st.markdown('**🎁 현재 교환 조건**')
    st.write(w['current_offer'])
if w['thanks']:
    st.markdown('**💌 감사의 편지**')
    st.write(w['thanks'])
status = w['status']
if role == 'parent' and status == 'pending':
    with st.form('reply_'+selected):
        decision = st.radio('부모님의 답변', ['허락','거절'], horizontal=True)
        message = st.text_area('답변의 이유', max_chars=700)
        sent = st.form_submit_button('이유와 함께 답하기', use_container_width=True)
    if sent: do(move, code, fid, selected, role, 'approve' if decision == '허락' else 'deny', message)
if role == 'child' and status == 'approved' and not w['thanks']:
    with st.form('thanks_'+selected):
        message = st.text_area('감사의 편지', max_chars=700)
        sent = st.form_submit_button('💌 감사 전하기', use_container_width=True)
    if sent: do(move, code, fid, selected, role, 'thanks', message)
if role == 'child' and status == 'denied':
    st.caption('내 안전이나 기본 권리를 거래 조건으로 삼지 마세요.')
    with st.form('offer_'+selected):
        message = st.text_area('내가 내어놓을 수 있는 조건', max_chars=700)
        sent = st.form_submit_button('🎁 딜 제안', use_container_width=True)
    if sent: do(move, code, fid, selected, role, 'offer', message)
my_turn = (role == 'parent' and status == 'offer_to_parent') or (role == 'child' and status == 'offer_to_child')
if my_turn:
    c1,c2 = st.columns(2)
    if c1.button('✅ 교환 수락', use_container_width=True): do(move, code, fid, selected, role, 'accept')
    if c2.button('❌ 교환 거부', use_container_width=True): do(move, code, fid, selected, role, 'reject')
    with st.form('counter_'+selected):
        message = st.text_area('역제안: 내가 제시하는 조건', max_chars=700)
        sent = st.form_submit_button('🔄 역제안 보내기', use_container_width=True)
    if sent: do(move, code, fid, selected, role, 'counter', message)
if status in ('accepted','rejected'):
    st.success('최종 결과: '+('교환 수락 · 합의했어요 🤝' if status == 'accepted' else '교환 거부 · 이번 대화는 종료됐어요 🛑'))
with st.expander('📜 대화 기록'):
    with db() as con:
        check_family(con, fid, code)
        events = con.execute('SELECT * FROM events WHERE wish_id=? ORDER BY id LIMIT 100', (selected,)).fetchall()
    for event in events:
        stamp = datetime.fromtimestamp(event['created']).strftime('%m-%d %H:%M')
        st.markdown(f"**{'🎒 자녀' if event['role']=='child' else '☕ 부모님'} · {esc(event['action'])}** · {stamp}")
        if event['detail']:
            st.write(event['detail'])
st.caption('💬 감정이 격할 땐 잠시 쉬었다 다시 이야기해요. 이 도구는 안전·건강·기본 권리를 흥정하기 위한 것이 아니에요.')
if st.button('↻ 새 답변 확인', use_container_width=True): st.rerun()
