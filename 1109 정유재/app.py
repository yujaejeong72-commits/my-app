"""나도 이제 고딩이에요! — 자녀와 부모가 차분하게 대화하는 제안 교환소.

배포
1. 이 파일과 requirements.txt를 같은 GitHub 저장소에 업로드합니다.
2. Supabase 프로젝트의 SQL Editor에서 SETUP_SQL 전체를 한 번 실행합니다.
3. Streamlit Community Cloud에서 app.py를 배포하고 Settings > Secrets에 다음을 입력합니다.
   SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
   SUPABASE_ANON_KEY = "YOUR_PUBLISHABLE_OR_ANON_KEY"
   APP_URL = "https://YOUR_APP.streamlit.app"  # 선택: QR 표시용
4. Supabase Authentication > URL Configuration의 Site URL을 배포 주소로 설정합니다.
   이메일 확인이 켜져 있다면 각자의 이메일 인증 후 로그인합니다.
주의: service_role 키를 GitHub나 이 앱에 넣지 마세요. 가족 초대 코드는 믿을 수 있는 상대에게만 전하세요.
"""

import html
import secrets
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client

SETUP_SQL = r"""
-- 새 Supabase 프로젝트에서 실행. 이미 같은 이름의 테이블이 있다면 먼저 확인하세요.
create table if not exists public.family_pairs (
  id uuid primary key default gen_random_uuid(),
  child_id uuid not null unique references auth.users(id),
  parent_id uuid unique references auth.users(id),
  invite_code text not null unique check (invite_code ~ '^[A-F0-9]{16}$'),
  created_at timestamptz not null default now(),
  check (parent_id is null or parent_id <> child_id)
);
create table if not exists public.wishes (
  id uuid primary key default gen_random_uuid(),
  pair_id uuid not null references public.family_pairs(id),
  child_id uuid not null references auth.users(id),
  title text not null check (char_length(title) between 1 and 70),
  reason text not null check (char_length(reason) between 1 and 700),
  status text not null default 'pending' check (status in
    ('pending','approved','denied','offer_to_parent','offer_to_child','accepted','rejected')),
  parent_reason text check (parent_reason is null or char_length(parent_reason) <= 700),
  thanks text check (thanks is null or char_length(thanks) <= 700),
  current_offer text check (current_offer is null or char_length(current_offer) <= 700),
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create table if not exists public.wish_events (
  id bigint generated always as identity primary key,
  wish_id uuid not null references public.wishes(id) on delete cascade,
  actor_id uuid not null references auth.users(id),
  action text not null,
  detail text not null default '',
  created_at timestamptz not null default now()
);
create index if not exists wishes_pair_recent on public.wishes(pair_id, created_at desc);
create index if not exists events_wish_order on public.wish_events(wish_id, id);
alter table public.family_pairs enable row level security;
alter table public.wishes enable row level security;
alter table public.wish_events enable row level security;
-- 읽기는 참여자에게만. 쓰기는 아래 검증된 RPC 함수로만 처리.
revoke all on public.family_pairs, public.wishes, public.wish_events from anon, authenticated;
grant select on public.family_pairs, public.wishes, public.wish_events to authenticated;
create policy "family_member_read" on public.family_pairs for select to authenticated
  using (child_id = (select auth.uid()) or parent_id = (select auth.uid()));
create policy "wish_member_read" on public.wishes for select to authenticated
  using (exists (select 1 from public.family_pairs p where p.id = pair_id
    and (p.child_id = (select auth.uid()) or p.parent_id = (select auth.uid()))));
create policy "event_member_read" on public.wish_events for select to authenticated
  using (exists (select 1 from public.wishes w join public.family_pairs p on p.id = w.pair_id
    where w.id = wish_id and (p.child_id = (select auth.uid()) or p.parent_id = (select auth.uid()))));

create or replace function public.create_family(p_code text) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  if auth.uid() is null then raise exception '로그인이 필요합니다'; end if;
  if p_code !~ '^[A-F0-9]{16}$' then raise exception '초대 코드 형식이 올바르지 않습니다'; end if;
  insert into public.family_pairs(child_id, invite_code) values (auth.uid(), p_code)
    returning id into v_id;
  return v_id;
end; $$;
create or replace function public.join_family(p_code text) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  if auth.uid() is null then raise exception '로그인이 필요합니다'; end if;
  if exists (select 1 from public.family_pairs where child_id = auth.uid() or parent_id = auth.uid())
    then raise exception '이미 가족에 연결된 계정입니다'; end if;
  update public.family_pairs set parent_id = auth.uid()
    where invite_code = p_code and parent_id is null and child_id <> auth.uid()
    returning id into v_id;
  if v_id is null then raise exception '코드가 틀렸거나 이미 사용되었습니다'; end if;
  return v_id;
end; $$;
create or replace function public.new_wish(p_pair uuid, p_title text, p_reason text) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  if not exists (select 1 from public.family_pairs where id = p_pair and child_id = auth.uid())
    then raise exception '자녀만 제안을 등록할 수 있습니다'; end if;
  p_title := btrim(p_title); p_reason := btrim(p_reason);
  if char_length(p_title) not between 1 and 70 or char_length(p_reason) not between 1 and 700
    then raise exception '제목 또는 이유의 글자 수를 확인해 주세요'; end if;
  insert into public.wishes(pair_id, child_id, title, reason)
    values (p_pair, auth.uid(), p_title, p_reason) returning id into v_id;
  insert into public.wish_events(wish_id, actor_id, action, detail)
    values(v_id, auth.uid(), '요청', p_reason);
  return v_id;
end; $$;
create or replace function public.move_wish(p_wish uuid, p_action text, p_text text default '') returns text
language plpgsql security definer set search_path = '' as $$
declare w public.wishes%rowtype; v_child uuid; v_parent uuid; v_role text; v_next text; v_detail text;
begin
  select * into w from public.wishes where id = p_wish for update;
  if not found then raise exception '요청을 찾을 수 없습니다'; end if;
  select child_id, parent_id into v_child, v_parent from public.family_pairs where id = w.pair_id;
  if auth.uid() = v_child then v_role := 'child';
  elsif auth.uid() = v_parent then v_role := 'parent';
  else raise exception '접근할 수 없습니다'; end if;
  v_detail := btrim(coalesce(p_text, ''));
  if char_length(v_detail) > 700 then raise exception '700자 이하로 입력해 주세요'; end if;
  if p_action in ('approve','deny') and v_role = 'parent' and w.status = 'pending' then
    if v_detail = '' then raise exception '허락/거절의 이유를 적어 주세요'; end if;
    v_next := case when p_action = 'approve' then 'approved' else 'denied' end;
    update public.wishes set status = v_next, parent_reason = v_detail, updated_at = now() where id = p_wish;
  elsif p_action = 'thanks' and v_role = 'child' and w.status = 'approved' and w.thanks is null then
    if v_detail = '' then raise exception '감사의 마음을 적어 주세요'; end if;
    v_next := 'approved';
    update public.wishes set thanks = v_detail, updated_at = now() where id = p_wish;
  elsif p_action = 'offer' and v_role = 'child' and w.status = 'denied' then
    if v_detail = '' then raise exception '제안할 내용을 적어 주세요'; end if;
    v_next := 'offer_to_parent';
    update public.wishes set status = v_next, current_offer = v_detail, updated_at = now() where id = p_wish;
  elsif p_action in ('accept','reject') and
    ((v_role = 'parent' and w.status = 'offer_to_parent') or
     (v_role = 'child' and w.status = 'offer_to_child')) then
    v_next := case when p_action = 'accept' then 'accepted' else 'rejected' end;
    update public.wishes set status = v_next, updated_at = now() where id = p_wish;
  elsif p_action = 'counter' and
    ((v_role = 'parent' and w.status = 'offer_to_parent') or
     (v_role = 'child' and w.status = 'offer_to_child')) then
    if v_detail = '' then raise exception '역제안 내용을 적어 주세요'; end if;
    v_next := case when v_role = 'parent' then 'offer_to_child' else 'offer_to_parent' end;
    update public.wishes set status = v_next, current_offer = v_detail, updated_at = now() where id = p_wish;
  else raise exception '현재 단계에서 할 수 없는 동작입니다'; end if;
  insert into public.wish_events(wish_id, actor_id, action, detail)
    values(p_wish, auth.uid(), p_action, v_detail);
  return v_next;
end; $$;
revoke all on function public.create_family(text), public.join_family(text),
  public.new_wish(uuid,text,text), public.move_wish(uuid,text,text) from public, anon;
grant execute on function public.create_family(text), public.join_family(text),
  public.new_wish(uuid,text,text), public.move_wish(uuid,text,text) to authenticated;
"""

st.set_page_config(page_title="나도 이제 고딩이에요!", page_icon="🎒", layout="centered", initial_sidebar_state="collapsed")
st.markdown("""<style>
.stApp{background:#fffaf3;color:#333048} .block-container{max-width:640px;padding:1rem 1rem 3rem}
.hero{background:linear-gradient(120deg,#ffe2ab,#ffd9dd);border:3px solid #433a5a;border-radius:22px;
box-shadow:4px 5px 0 #433a5a;padding:16px;margin-bottom:10px}
.hero h1{font-size:1.65rem;margin:0;color:#322d46}.hero p{margin:5px 0 0;font-size:.88rem}
.ticket{background:#fff;border:2px solid #514968;border-radius:17px;padding:12px;margin:10px 0;box-shadow:3px 3px 0 #ddd3ec}
.tag{font-size:.78rem;color:#675d7c}.bubble{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.45;margin:6px 0}
.stButton>button,div[data-testid="stFormSubmitButton"] button{border-radius:13px;border:2px solid #514968;min-height:42px;font-weight:700}
[data-testid="stSidebar"]{background:#fff4e5} .stTextArea textarea{border-radius:12px}
</style>""", unsafe_allow_html=True)


def safe(value):
    return html.escape(str(value or ""))


def notice(text):
    st.error("처리하지 못했어요. 입력값이나 로그인 상태를 확인해 주세요. " + str(text)[:180])


def run_rpc(name, params, success="저장했어요!"):
    try:
        st.session_state.db.rpc(name, params).execute()
        st.toast(success)
        st.rerun()
    except Exception as exc:
        notice(exc)


def current_user():
    try:
        result = st.session_state.db.auth.get_user()
        return result.user if result else None
    except Exception:
        return None


st.markdown('<div class="hero"><h1>🎒 나도 이제 고딩이에요!</h1><p>말하기 어려운 마음을, 작은 대화 카드로 꺼내 봐요.</p></div>', unsafe_allow_html=True)

try:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_ANON_KEY"]
except Exception:
    st.warning("먼저 Supabase를 연결해 주세요. app.py 맨 위의 배포 안내와 SETUP_SQL을 확인하세요.")
    st.stop()
if "db" not in st.session_state:
    st.session_state.db = create_client(url, key)
db = st.session_state.db
user = current_user()

if user is None:
    st.caption("자녀와 부모님은 **각자 다른 이메일 계정**으로 가입해 주세요.")
    tab_login, tab_signup = st.tabs(["🔑 로그인", "🌱 가입"])
    with tab_login:
        with st.form("login"):
            email = st.text_input("이메일", key="login_email")
            password = st.text_input("비밀번호", type="password", key="login_password")
            submitted = st.form_submit_button("로그인", use_container_width=True)
        if submitted:
            try:
                db.auth.sign_in_with_password({"email": email.strip(), "password": password})
                st.rerun()
            except Exception as exc:
                notice(exc)
    with tab_signup:
        with st.form("signup"):
            new_email = st.text_input("이메일", key="new_email")
            new_password = st.text_input("비밀번호 (6자 이상)", type="password", key="new_password")
            submitted = st.form_submit_button("계정 만들기", use_container_width=True)
        if submitted:
            try:
                if len(new_password) < 6:
                    st.error("비밀번호는 6자 이상으로 만들어 주세요.")
                else:
                    result = db.auth.sign_up({"email": new_email.strip(), "password": new_password})
                    if result.session:
                        st.rerun()
                    else:
                        st.success("가입 메일을 확인한 뒤 로그인해 주세요.")
            except Exception as exc:
                notice(exc)
    st.stop()

with st.sidebar:
    st.caption("🔒 계정: " + safe(user.email))
    if st.button("로그아웃", use_container_width=True):
        try:
            db.auth.sign_out()
        finally:
            st.session_state.pop("db", None)
            st.rerun()
    with st.expander("배포 주소 QR"):
        app_url = st.secrets.get("APP_URL", "")
        if app_url:
            import io
            import qrcode
            img = qrcode.make(app_url)
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            st.image(buffer.getvalue(), width=180)
            st.download_button("QR 저장", buffer.getvalue(), "family_dialogue_qr.png", "image/png")
            st.caption("QR은 접속 주소만 담아요. 로그인해야 대화를 볼 수 있습니다.")
        else:
            st.caption("Secrets에 APP_URL을 설정하면 QR이 표시됩니다.")

try:
    found = db.table("family_pairs").select("id,child_id,parent_id,invite_code").or_(f"child_id.eq.{user.id},parent_id.eq.{user.id}").limit(1).execute().data
except Exception as exc:
    notice(exc)
    st.info("Supabase SQL Editor에서 app.py의 SETUP_SQL을 먼저 실행했는지 확인해 주세요.")
    st.stop()

if not found:
    st.subheader("👋 가족 연결하기")
    choice = st.radio("나는", ["자녀", "부모님"], horizontal=True)
    if choice == "자녀":
        st.caption("연결 코드를 만든 뒤 부모님께 직접 전달해 주세요.")
        if st.button("가족 초대 코드 만들기", use_container_width=True):
            run_rpc("create_family", {"p_code": secrets.token_hex(8).upper()}, "가족 코드가 만들어졌어요!")
    else:
        with st.form("join"):
            code = st.text_input("자녀에게 받은 16자리 코드", max_chars=16)
            joining = st.form_submit_button("가족과 연결", use_container_width=True)
        if joining:
            run_rpc("join_family", {"p_code": code.strip().upper()}, "연결되었어요!")
    st.stop()

pair = found[0]
is_child = pair["child_id"] == user.id
st.caption("내 역할: " + ("🎒 자녀" if is_child else "☕ 부모님"))
if pair["parent_id"] is None:
    st.info("부모님이 연결되면 서로의 글을 볼 수 있어요.")
    st.code(pair["invite_code"], language=None)
    st.caption("이 코드를 부모님께 안전하게 전달해 주세요. 연결되기 전에는 다른 사람이 코드를 사용하지 않도록 주의해요.")

try:
    wishes = db.table("wishes").select("id,title,reason,status,parent_reason,thanks,current_offer,created_at").eq("pair_id", pair["id"]).order("created_at", desc=True).limit(50).execute().data or []
except Exception as exc:
    notice(exc)
    st.stop()

labels = {"pending": "📮 부모님 답변 기다리는 중", "approved": "🌼 허락", "denied": "🌧️ 거절 · 대화 가능", "offer_to_parent": "🎁 부모님 교환 답변 기다리는 중", "offer_to_child": "🎁 자녀 교환 답변 기다리는 중", "accepted": "🤝 교환 수락 · 합의 완료", "rejected": "🛑 교환 거부 · 종료"}
actions = {"요청": "요청 작성", "approve": "허락", "deny": "거절", "thanks": "감사의 편지", "offer": "첫 교환 제안", "counter": "역제안", "accept": "교환 수락", "reject": "교환 거부"}

if is_child:
    with st.expander("✍️ 새로운 바람 적기", expanded=not wishes):
        with st.form("new_wish", clear_on_submit=True):
            title = st.text_input("바라는 것", placeholder="예: 친구들과 토요일에 영화 보기", max_chars=70)
            reason = st.text_area("왜 원하는지 / 부모님이 걱정하실 점", height=100, max_chars=700)
            created = st.form_submit_button("부모님께 전하기 📮", use_container_width=True)
        if created:
            run_rpc("new_wish", {"p_pair": pair["id"], "p_title": title, "p_reason": reason}, "마음을 전했어요!")

st.subheader("💌 대화 카드")
if not wishes:
    st.info("아직 대화 카드가 없어요. 자녀가 먼저 바라는 것을 적으면 시작됩니다.")
    st.stop()

# 한 화면에서 선택한 대화 카드 하나만 펼쳐 긴 대화의 무한 스크롤을 줄입니다.
options = {w["id"]: f"{w['title']} · {labels.get(w['status'], w['status'])}" for w in wishes}
chosen = st.selectbox("대화 선택 (최근 50개)", list(options), format_func=lambda k: options[k])
w = next(item for item in wishes if item["id"] == chosen)
st.markdown('<div class="ticket"><strong>💌 ' + safe(w["title"]) + '</strong><div class="tag">' + safe(labels.get(w["status"], w["status"])) + '</div><div class="bubble">' + safe(w["reason"]) + '</div></div>', unsafe_allow_html=True)
if w["parent_reason"]:
    st.markdown("**☕ 부모님의 이유**")
    st.write(w["parent_reason"])
if w["current_offer"]:
    st.markdown("**🎁 현재 교환 제안**")
    st.write(w["current_offer"])
if w["thanks"]:
    st.markdown("**💌 감사의 편지**")
    st.write(w["thanks"])

status = w["status"]
if not is_child and status == "pending":
    st.caption("허락·거절 어느 쪽이든, 이유를 먼저 전해 주세요.")
    with st.form("reply_" + chosen):
        decision = st.radio("답변", ["허락", "거절"], horizontal=True)
        reply = st.text_area("그렇게 생각한 이유", max_chars=700)
        sent = st.form_submit_button("이유와 함께 답하기", use_container_width=True)
    if sent:
        run_rpc("move_wish", {"p_wish": chosen, "p_action": "approve" if decision == "허락" else "deny", "p_text": reply})

if is_child and status == "approved" and not w["thanks"]:
    with st.form("thanks_" + chosen):
        thank_you = st.text_area("부모님께 감사의 편지", max_chars=700)
        sent = st.form_submit_button("💌 감사의 편지 보내기", use_container_width=True)
    if sent:
        run_rpc("move_wish", {"p_wish": chosen, "p_action": "thanks", "p_text": thank_you})

if is_child and status == "denied":
    st.caption("거절을 받아들이거나, 실천 가능한 조건을 제안할 수 있어요. 안전이나 존중은 거래 대상이 아니에요.")
    with st.form("offer_" + chosen):
        offer = st.text_area("내가 제안하는 조건", placeholder="예: 귀가 시간을 정하고 도착하면 연락할게요", max_chars=700)
        sent = st.form_submit_button("🎁 딜 제안 보내기", use_container_width=True)
    if sent:
        run_rpc("move_wish", {"p_wish": chosen, "p_action": "offer", "p_text": offer})

my_turn = (not is_child and status == "offer_to_parent") or (is_child and status == "offer_to_child")
if my_turn:
    st.caption("제안을 읽고 선택해 주세요. 역제안은 서로 납득할 수 있는 조건으로 적어 주세요.")
    c1, c2 = st.columns(2)
    if c1.button("✅ 교환 수락", key="accept_" + chosen, use_container_width=True):
        run_rpc("move_wish", {"p_wish": chosen, "p_action": "accept", "p_text": ""}, "합의가 기록되었어요!")
    if c2.button("❌ 교환 거부", key="reject_" + chosen, use_container_width=True):
        run_rpc("move_wish", {"p_wish": chosen, "p_action": "reject", "p_text": ""}, "결과가 기록되었어요.")
    with st.form("counter_" + chosen):
        counter = st.text_area("역제안: 내가 내어놓을 수 있는 조건", max_chars=700)
        sent = st.form_submit_button("🔄 역제안 보내기", use_container_width=True)
    if sent:
        run_rpc("move_wish", {"p_wish": chosen, "p_action": "counter", "p_text": counter})

if status in ("accepted", "rejected"):
    st.success("최종 결과: " + ("서로 제안에 합의했어요 🤝" if status == "accepted" else "이번 교환은 성사되지 않았어요. 🛑"))

with st.expander("📜 대화 기록 · 이유와 제안의 흐름"):
    try:
        history = db.table("wish_events").select("actor_id,action,detail,created_at").eq("wish_id", chosen).order("id").limit(100).execute().data or []
        for event in history:
            who = "🎒 자녀" if event["actor_id"] == pair["child_id"] else "☕ 부모님"
            when = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
            st.markdown(f"**{who} · {actions.get(event['action'], event['action'])}** · {when}")
            if event["detail"]:
                st.write(event["detail"])
    except Exception as exc:
        notice(exc)

st.caption("💬 감정이 격할 땐 잠시 쉬었다가 다시 이야기해도 괜찮아요. 이 앱은 대화의 도구이지, 안전·건강·기본적인 권리를 흥정하는 도구가 아닙니다.")
if st.button("↻ 새 답변 확인", use_container_width=True):
    st.rerun()
