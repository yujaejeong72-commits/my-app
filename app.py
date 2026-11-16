"""나도 이제 고딩이에요! — 별도 서비스/Secrets 없이 실행하는 Streamlit 데모.
GitHub에 app.py, requirements.txt 업로드 → Streamlit Community Cloud에서 app.py 배포.
데이터는 실행 서버의 SQLite 파일에 저장됩니다. Community Cloud 재시작/재배포 시
초기화될 수 있으므로 장기 보관이나 민감한 가족 대화용으로 사용하지 마세요.
QR은 배포 후 브라우저 주소를 입력해 생성합니다. QR은 로그인 수단이 아닙니다.
"""
import hashlib
import html
import io
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import qrcode
import streamlit as st

DB_PATH = Path('family_dialogue.sqlite3')

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
        CREATE TABLE IF NOT EXISTS families (
            id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL,
            child_salt TEXT NOT NULL, child_hash TEXT NOT NULL,
            parent_salt TEXT NOT NULL, parent_hash TEXT NOT NULL,
            created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS wishes (
            id TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES families(id),
            title TEXT NOT NULL, reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', parent_reason TEXT,
            thanks TEXT, current_offer TEXT, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, wish_id TEXT NOT NULL,
            role TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL,
            created REAL NOT NULL);
        ''')

def hashed(pin, salt):
    return hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(salt), 250000).hex()

def valid_pin(pin):
    return pin.isdigit() and 6 <= len(pin) <= 12

def create_family(child_pin, parent_pin):
    if not valid_pin(child_pin) or not valid_pin(parent_pin) or child_pin == parent_pin:
        raise ValueError('서로 다른 숫자 비밀번호(각 6~12자리)를 입력해 주세요.')
    with db() as con:
        for _ in range(5):
            code = secrets.token_hex(4).upper()
            if not con.execute('SELECT 1 FROM families WHERE code=?', (code,)).fetchone():
                break
        else:
            raise ValueError('코드 생성에 실패했어요. 다시 시도해 주세요.')
        cs, ps = secrets.token_hex(16), secrets.token_hex(16)
        fid = uuid.uuid4().hex
        con.execute('INSERT INTO families VALUES (?,?,?,?,?,?,?,?)',
                    (fid, code, cs, hashed(child_pin, cs), ps, hashed(parent_pin, ps), time.time()))
    return fid, code

def sign_in(code, role, pin):
    with db() as con:
        row = con.execute('SELECT * FROM families WHERE code=?', (code.strip().upper(),)).fetchone()
    if not row or role not in ('child', 'parent'):
        return None
    salt = row[role + '_salt']
    return row['id'] if secrets.compare_digest(hashed(pin, salt), row[role + '_hash']) else None

def add_event(con, wish_id, role, action, detail=''):
    con.execute('INSERT INTO events(wish_id,role,action,detail,created) VALUES(?,?,?,?,?)',
                (wish_id, role, action, detail, time.time()))

def new_wish(fid, title, reason):
    title, reason = title.strip(), reason.strip()
    if not (1 <= len(title) <= 70 and 1 <= len(reason) <= 700):
        raise ValueError('제목과 이유를 입력해 주세요 (각각 70자·700자 이내).')
    wid = uuid.uuid4().hex
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('INSERT INTO wishes(id,family_id,title,reason,created) VALUES(?,?,?,?,?)',
                    (wid, fid, title, reason, time.time()))
        add_event(con, wid, 'child', '요청', reason)

def move(fid, wid, role, action, text=''):
    text = text.strip()
    if len(text) > 700:
        raise ValueError('내용은 700자 이내로 입력해 주세요.')
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM wishes WHERE id=? AND family_id=?', (wid, fid)).fetchone()
        if row is None:
            raise ValueError('대화 카드를 찾을 수 없어요.')
        status = row['status']
        updates = {}
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
              (role == 'child' and status == 'offer_to_child')) and action in ('accept', 'reject', 'counter'):
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

def esc(text):
    return html.escape(str(text or ''))

st.set_page_config(page_title='나도 이제 고딩이에요!', page_icon='🎒', layout='centered')
st.markdown('''<style>
.stApp{background:#fffaf4;color:#302943}.block-container{max-width:640px;padding:1rem 1rem 2rem}
.hero{border:3px solid #514468;border-radius:23px;background:linear-gradient(110deg,#ffe6b0,#ffdae1);padding:18px;box-shadow:5px 6px 0 #514468}
.hero h1{font-size:1.62rem;margin:0}.hero p{margin:9px 0 0;font-size:.94rem}
.card{background:white;border:2px solid #514468;border-radius:17px;padding:14px;box-shadow:3px 4px 0 #dfd2e9;margin:12px 0}
.body{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.5}
.stButton button,div[data-testid="stFormSubmitButton"] button{border:2px solid #514468;border-radius:12px;min-height:42px;font-weight:700}
</style>''', unsafe_allow_html=True)
st.markdown('<div class="hero"><h1>🎒 나도 이제 고딩이에요!</h1><p>말하기 어려운 마음을, 작은 대화 카드로 꺼내 봐요.</p></div>', unsafe_allow_html=True)
try:
    setup()
except sqlite3.Error:
    st.error('저장 공간에 접근할 수 없어요. 배포 환경의 쓰기 권한을 확인해 주세요.')
    st.stop()

if not st.session_state.get('family'):
    st.caption('같은 가족 코드로 자녀와 부모님이 각자의 비밀번호로 접속해요.')
    make, enter = st.tabs(['🌱 가족 방 만들기', '🔑 기존 가족 방 입장'])
    with make:
        with st.form('make'):
            cp = st.text_input('자녀 비밀번호 (숫자 6~12자리)', type='password')
            pp = st.text_input('부모님 비밀번호 (다른 숫자 6~12자리)', type='password')
            submitted = st.form_submit_button('가족 방 만들기', use_container_width=True)
        if submitted:
            try:
                fid, code = create_family(cp, pp)
                st.session_state.family, st.session_state.role = fid, 'child'
                st.session_state.code = code
                st.rerun()
            except (ValueError, sqlite3.Error) as exc:
                st.error(str(exc))
    with enter:
        with st.form('enter'):
            code = st.text_input('가족 코드 (8자리)', max_chars=8)
            role_label = st.radio('나는', ['자녀', '부모님'], horizontal=True)
            pin = st.text_input('내 비밀번호', type='password')
            submitted = st.form_submit_button('입장하기', use_container_width=True)
        if submitted:
            role = 'child' if role_label == '자녀' else 'parent'
            fid = sign_in(code, role, pin)
            if fid:
                st.session_state.family, st.session_state.role = fid, role
                st.session_state.code = code.strip().upper()
                st.rerun()
            else:
                st.error('가족 코드·역할·비밀번호를 확인해 주세요.')
    st.warning('데모 저장소는 배포 서버 재시작·재배포 때 지워질 수 있어요. 민감한 내용은 입력하지 마세요.')
    st.stop()

fid, role = st.session_state.family, st.session_state.role
with st.sidebar:
    st.write('🎒 자녀' if role == 'child' else '☕ 부모님')
    st.write('가족 코드: **' + esc(st.session_state.code) + '**')
    st.caption('부모님께는 가족 코드와 부모님 비밀번호만 따로 전달하세요. 두 비밀번호를 함께 공유하지 마세요.')
    if st.button('로그아웃', use_container_width=True):
        for key in ('family', 'role', 'code'):
            st.session_state.pop(key, None)
        st.rerun()
    with st.expander('접속 QR 만들기'):
        app_url = st.text_input('배포한 앱 주소', placeholder='https://example.streamlit.app')
        if app_url.startswith('https://'):
            image = qrcode.make(app_url)
            out = io.BytesIO()
            image.save(out, format='PNG')
            st.image(out.getvalue(), width=170)
            st.download_button('QR 다운로드', out.getvalue(), 'app_qr.png', 'image/png')
        else:
            st.caption('배포한 https 주소를 입력하면 QR이 나타나요. QR만으로 로그인되지는 않아요.')

st.caption('🎒 자녀' if role == 'child' else '☕ 부모님')
if role == 'child':
    with st.expander('✍️ 내가 바라는 것 전하기'):
        with st.form('wish', clear_on_submit=True):
            title = st.text_input('바라는 것', max_chars=70)
            reason = st.text_area('왜 바라는지 / 걱정되는 부분', max_chars=700)
            sent = st.form_submit_button('📮 부모님께 전하기', use_container_width=True)
        if sent: do(new_wish, fid, title, reason)

with db() as con:
    wishes = con.execute('SELECT * FROM wishes WHERE family_id=? ORDER BY created DESC LIMIT 50', (fid,)).fetchall()
labels = {'pending':'📮 부모님 답변 기다리는 중','approved':'🌼 허락','denied':'🌧️ 거절 · 딜 가능',
          'offer_to_parent':'🎁 부모님 응답 기다리는 중','offer_to_child':'🎁 자녀 응답 기다리는 중',
          'accepted':'🤝 교환 수락 · 종료','rejected':'🛑 교환 거부 · 종료'}
if not wishes:
    st.info('아직 대화가 없어요. 자녀가 먼저 바라는 것을 적으면 시작됩니다.')
    st.stop()
st.subheader('💌 대화 카드')
options = {w['id']: f"{w['title']} · {labels.get(w['status'], w['status'])}" for w in wishes}
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
        decision = st.radio('부모님의 답변', ['허락', '거절'], horizontal=True)
        message = st.text_area('답변의 이유', max_chars=700)
        submitted = st.form_submit_button('이유와 함께 답하기', use_container_width=True)
    if submitted: do(move, fid, selected, role, 'approve' if decision == '허락' else 'deny', message)
if role == 'child' and status == 'approved' and not w['thanks']:
    with st.form('thanks_'+selected):
        message = st.text_area('감사의 편지', max_chars=700)
        submitted = st.form_submit_button('💌 감사 전하기', use_container_width=True)
    if submitted: do(move, fid, selected, role, 'thanks', message)
if role == 'child' and status == 'denied':
    st.caption('거절 뒤에는 실천 가능한 조건을 제안할 수 있어요. 내 안전이나 권리를 거래 조건으로 삼지 마세요.')
    with st.form('offer_'+selected):
        message = st.text_area('내가 내어놓을 수 있는 조건', max_chars=700)
        submitted = st.form_submit_button('🎁 딜 제안', use_container_width=True)
    if submitted: do(move, fid, selected, role, 'offer', message)
my_turn = (role == 'parent' and status == 'offer_to_parent') or (role == 'child' and status == 'offer_to_child')
if my_turn:
    c1,c2 = st.columns(2)
    if c1.button('✅ 교환 수락', use_container_width=True): do(move, fid, selected, role, 'accept')
    if c2.button('❌ 교환 거부', use_container_width=True): do(move, fid, selected, role, 'reject')
    with st.form('counter_'+selected):
        message = st.text_area('역제안: 내가 제시하는 조건', max_chars=700)
        submitted = st.form_submit_button('🔄 역제안 보내기', use_container_width=True)
    if submitted: do(move, fid, selected, role, 'counter', message)
if status in ('accepted','rejected'):
    st.success('최종 결과: '+('교환 수락 · 합의했어요 🤝' if status == 'accepted' else '교환 거부 · 이번 대화는 종료됐어요 🛑'))
with st.expander('📜 대화 기록'):
    with db() as con:
        events = con.execute('SELECT * FROM events WHERE wish_id=? ORDER BY id LIMIT 100', (selected,)).fetchall()
    for event in events:
        when = datetime.fromtimestamp(event['created']).strftime('%m-%d %H:%M')
        st.markdown(f"**{'🎒 자녀' if event['role']=='child' else '☕ 부모님'} · {esc(event['action'])}** · {when}")
        if event['detail']:
            st.write(event['detail'])
st.caption('💬 감정이 격할 땐 잠시 쉬었다 다시 이야기해요. 이 도구는 안전·건강·기본 권리를 흥정하기 위한 것이 아니에요.')
if st.button('↻ 새 답변 확인', use_container_width=True): st.rerun()
