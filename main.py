import asyncio
import re
import base64
import io
from datetime import datetime, time
import pytz
from telethon import TelegramClient
import requests
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import yfinance as yf
from playwright.async_api import async_playwright

import os

# ===== 설정 =====
TELEGRAM_API_ID = os.environ.get("TELEGRAM_API_ID", "30982164")
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "fd6adb556a44d0941aeb3f91bdd440cf")
TELEGRAM_CHANNEL = "한투증권 투자전략 김대준"
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "xoxb-9656565174614-11654721376550-8gV1gqA42lg2RuQnxRGiRqQB")
SLACK_USER_ID = os.environ.get("SLACK_USER_ID", "U0BKBBCG280")
SLACK_DAILY_CHANNEL_ID = os.environ.get("SLACK_DAILY_CHANNEL_ID", "C0BJKDQC6HX")

TEST_MODE = False  # True: 본인 DM으로 전송 / False: 텔레그램_데일리 채널로 전송

KST = pytz.timezone("Asia/Seoul")
TARGET_START = time(16, 30)
TARGET_END = time(17, 30)


# ===== 텔레그램 메시지 가져오기 =====
async def get_today_messages():
    async with TelegramClient("session", TELEGRAM_API_ID, TELEGRAM_API_HASH) as client:
        channel = None
        async for dialog in client.iter_dialogs():
            if TELEGRAM_CHANNEL in dialog.name:
                channel = dialog.entity
                print(f"채널 찾음: {dialog.name}")
                break

        if channel is None:
            print("채널을 찾지 못했습니다.")
            return []

        today = datetime.now(KST).date()
        messages = []
        async for message in client.iter_messages(channel, limit=500):
            if not message.text:
                continue
            msg_time = message.date.astimezone(KST)
            if msg_time.date() > today:
                continue
            if msg_time.date() < today:
                break
            if TARGET_START <= msg_time.time() <= TARGET_END and '시장 정리' in message.text:
                messages.append(message.text)

        return messages


# ===== 마침표 기준 줄바꿈 =====
def add_line_breaks(text):
    # '마감.' 뒤에는 줄바꿈 안 함, 그 외 '. ' 뒤에는 줄바꿈 추가
    result = re.sub(r'(?<!마감)\.\s+', '.<br>', text)
    return result


# ===== 텍스트 파싱 =====
def parse_message(full_text):
    data = {
        'date': datetime.now(KST).strftime("%Y년 %m월 %d일"),
        'kospi_value': '-', 'kospi_change': '-', 'kospi_class': 'up', 'kospi_arrow': '▲',
        'kosdaq_value': '-', 'kosdaq_change': '-', 'kosdaq_class': 'up', 'kosdaq_arrow': '▲',
        'kospi_chart': '', 'kosdaq_chart': '',
        'kospi_comment': '', 'kosdaq_comment': '',
        'issue_text': '',
        'foreign_flow': '-', 'foreign_class': 'up',
        'individual_flow': '-', 'individual_class': 'down',
        'institution_flow': '-', 'institution_class': 'up',
        'sector_text': '', 'stock_text': '',
        'exchange_rate': '-', 'exchange_change': '-', 'exchange_class': 'down',
        'bond_rate': '-', 'bond_change': '-', 'bond_class': 'up',
        'footnote': '',
    }

    # KOSPI / KOSDAQ 값 추출
    kospi_m = re.search(r'KOSPI\s+([0-9,]+\.?\d*)[p점]?\s*\(([+-][0-9.]+%)\)', full_text, re.IGNORECASE)
    if kospi_m:
        data['kospi_value'] = kospi_m.group(1)
        data['kospi_change'] = kospi_m.group(2)
        is_up = '+' in kospi_m.group(2)
        data['kospi_class'] = 'up' if is_up else 'down'
        data['kospi_arrow'] = '▲' if is_up else '▼'

    kosdaq_m = re.search(r'KOSDAQ\s+([0-9,]+\.?\d*)[p점]?\s*\(([+-][0-9.]+%)\)', full_text, re.IGNORECASE)
    if kosdaq_m:
        data['kosdaq_value'] = kosdaq_m.group(1)
        data['kosdaq_change'] = kosdaq_m.group(2)
        is_up = '+' in kosdaq_m.group(2)
        data['kosdaq_class'] = 'up' if is_up else 'down'
        data['kosdaq_arrow'] = '▲' if is_up else '▼'

    lines = full_text.split('\n')

    # 지수 코멘트: ■ 코스피는... / ■ 코스닥은... 분리
    kospi_lines = []
    kosdaq_lines = []
    current_index = None
    for line in lines:
        stripped = re.sub(r'^■\s*', '', line).strip()
        if stripped.startswith('코스피는'):
            current_index = 'kospi'
            kospi_lines.append(stripped)
        elif stripped.startswith('코스닥은'):
            current_index = 'kosdaq'
            kosdaq_lines.append(stripped)
        elif current_index and stripped and not re.match(r'^(■|\*|cf\))', line):
            if not any(k in stripped for k in ['외국인', '개인', '기관', '원/달러', '국고채', 'KOSPI', 'KOSDAQ']):
                if current_index == 'kospi':
                    kospi_lines.append(stripped)
                else:
                    kosdaq_lines.append(stripped)
        elif re.match(r'^■', line) and not (stripped.startswith('코스피는') or stripped.startswith('코스닥은')):
            current_index = None
    data['kospi_comment'] = add_line_breaks(' '.join(kospi_lines))
    data['kosdaq_comment'] = add_line_breaks(' '.join(kosdaq_lines))

    # 이슈: * 로 시작하는 줄
    issue_lines = []
    in_issue = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('*'):
            in_issue = True
            issue_lines.append(re.sub(r'^\*\s*', '', stripped))
        elif in_issue and stripped and not stripped.startswith('■') and not stripped.startswith('cf)'):
            issue_lines.append(stripped)
        elif stripped.startswith('■') and issue_lines:
            in_issue = False
    data['issue_text'] = add_line_breaks('<br>'.join(issue_lines))

    # 수급: 외국인 X억원, 개인 X억원, 기관 X억원
    f = re.search(r'외국인\s+([+-][0-9,]+억원)', full_text)
    i = re.search(r'개인\s+([+-][0-9,]+억원)', full_text)
    g = re.search(r'기관\s+([+-][0-9,]+억원)', full_text)
    if f:
        data['foreign_flow'] = f.group(1)
        data['foreign_class'] = 'up' if f.group(1).startswith('+') else 'down'
    if i:
        data['individual_flow'] = i.group(1)
        data['individual_class'] = 'up' if i.group(1).startswith('+') else 'down'
    if g:
        data['institution_flow'] = g.group(1)
        data['institution_class'] = 'up' if g.group(1).startswith('+') else 'down'

    # 업종별: ■ 코스피 업종 or 업종별 포함 줄
    sector_lines = []
    in_sector = False
    for line in lines:
        stripped = line.strip()
        if re.match(r'^■\s*(코스피\s*)?업종', stripped):
            in_sector = True
            content = re.sub(r'^■\s*(코스피\s*)?업종[^\s]*\s*', '', stripped).strip()
            if content:
                sector_lines.append(content)
        elif in_sector and stripped and not stripped.startswith('■') and not stripped.startswith('cf)'):
            sector_lines.append(stripped)
        elif stripped.startswith('■') and in_sector:
            in_sector = False
    data['sector_text'] = add_line_breaks('<br>'.join(sector_lines))

    # 특징주: ■ 특징주 포함 줄
    stock_lines = []
    in_stock = False
    for line in lines:
        stripped = line.strip()
        if re.search(r'특징주', stripped) and stripped.startswith('■'):
            in_stock = True
            content = re.sub(r'^■\s*특징주[:\s]*', '', stripped).strip()
            if content:
                stock_lines.append(content)
        elif in_stock and stripped and not stripped.startswith('■') and not stripped.startswith('cf)'):
            stock_lines.append(stripped)
        elif stripped.startswith('■') and in_stock:
            in_stock = False
    data['stock_text'] = add_line_breaks('<br>'.join(stock_lines))

    # 환율·금리
    er = re.search(r'원/달러\s*환율[:\s]+([0-9,]+\.?\d*원)\s*\(([^)]+)\)', full_text)
    if er:
        data['exchange_rate'] = er.group(1)
        data['exchange_change'] = er.group(2)
        data['exchange_class'] = 'down' if '-' in er.group(2) else 'up'

    br = re.search(r'국고채\s*3년물[:\s]+([0-9.]+%)\s*\(([^)]+)\)', full_text)
    if br:
        data['bond_rate'] = br.group(1)
        data['bond_change'] = br.group(2)
        data['bond_class'] = 'up' if '+' in br.group(2) else 'down'

    # cf) 주석
    cf = re.search(r'cf\)[^\n]+', full_text)
    if cf:
        data['footnote'] = cf.group(0)

    return data


# ===== 시장 데이터 가져오기 (Yahoo Finance) =====
def get_market_data():
    try:
        kospi = yf.download("^KS11", period="2d", interval="1m", progress=False)
        kosdaq = yf.download("^KQ11", period="2d", interval="1m", progress=False)
        return kospi, kosdaq
    except Exception as e:
        print(f"시장 데이터 오류: {e}")
        return None, None


# ===== 라인 차트 → base64 =====
def create_chart_base64(df, is_up):
    def empty_chart():
        fig, ax = plt.subplots(figsize=(3.5, 0.65), dpi=150)
        fig.patch.set_facecolor("#2a2a2a")
        ax.set_facecolor("#2a2a2a")
        ax.axis('off')
        buf = io.BytesIO()
        fig.savefig(buf, format='PNG', bbox_inches='tight', pad_inches=0, facecolor="#2a2a2a", dpi=150)
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode('utf-8')

    if df is None or df.empty:
        return empty_chart()

    try:
        prices = df["Close"].squeeze().dropna()
    except Exception:
        return empty_chart()

    if len(prices) < 2:
        return empty_chart()

    line_color = "#FD2929" if is_up else "#47A5ED"

    # 날짜 기준으로 어제/오늘 분리
    dates = prices.index.normalize().unique()
    if len(dates) >= 2:
        today = dates[-1]
        prev_prices = prices[prices.index.normalize() < today]
        today_prices = prices[prices.index.normalize() == today]
    else:
        prev_prices = prices.iloc[:0]  # 빈 Series
        today_prices = prices

    if len(today_prices) < 2:
        return empty_chart()

    baseline = float(today_prices.iloc[0])
    total_len = len(prev_prices) + len(today_prices)
    x_prev = list(range(len(prev_prices)))
    x_today = list(range(len(prev_prices), total_len))
    y_today = today_prices.values.flatten()

    all_values = list(prev_prices.values.flatten()) + list(y_today)
    ymin = min(all_values) * 0.9985
    ymax = max(all_values) * 1.0015

    fig, ax = plt.subplots(figsize=(3.5, 0.65), dpi=150)
    fig.patch.set_facecolor("#2a2a2a")
    ax.set_facecolor("#2a2a2a")

    # 어제 데이터 - 회색
    if len(prev_prices) > 0:
        ax.plot(x_prev, prev_prices.values.flatten(), color="#888888", linewidth=1.2)

    # 오늘 시가 기준 점선 baseline
    ax.axhline(y=baseline, color="#666666", linewidth=0.7, linestyle="--", alpha=0.6)

    # 오늘 데이터 - 컬러
    ax.plot(x_today, y_today, color=line_color, linewidth=1.8)
    ax.fill_between(x_today, y_today, baseline, color=line_color, alpha=0.18)

    ax.set_xlim(0, total_len - 1)
    ax.set_ylim(ymin, ymax)
    ax.axis('off')
    fig.tight_layout(pad=0)

    buf = io.BytesIO()
    fig.savefig(buf, format='PNG', bbox_inches='tight', pad_inches=0, facecolor="#2a2a2a", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


# ===== HTML → 이미지 (playwright) =====
async def render_html(html_content):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 800, "height": 1200}, device_scale_factor=2)
        await page.set_content(html_content, wait_until="networkidle")
        height = await page.evaluate("document.body.scrollHeight")
        await page.set_viewport_size({"width": 800, "height": height})
        screenshot = await page.screenshot(full_page=True)
        await browser.close()
        return screenshot


# ===== 오늘 이미 전송했는지 확인 =====
def already_sent_today():
    today_date_str = datetime.now(KST).strftime("%Y년 %m월 %d일")
    channel_id = SLACK_DAILY_CHANNEL_ID if not TEST_MODE else None

    if TEST_MODE:
        dm_response = requests.post(
            "https://slack.com/api/conversations.open",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json={"users": SLACK_USER_ID}
        )
        dm_data = dm_response.json()
        if not dm_data.get("ok"):
            return False
        channel_id = dm_data["channel"]["id"]

    response = requests.get(
        "https://slack.com/api/conversations.history",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        params={"channel": channel_id, "limit": 20}
    )
    data = response.json()
    if not data.get("ok"):
        return False

    for msg in data.get("messages", []):
        if "한국 시장 마감 정리" in msg.get("text", "") and today_date_str in msg.get("text", ""):
            return True
    return False


# ===== 슬랙 DM 채널 ID 가져오기 =====
def get_dm_channel_id():
    dm_response = requests.post(
        "https://slack.com/api/conversations.open",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"users": SLACK_USER_ID}
    )
    dm_data = dm_response.json()
    if not dm_data.get("ok"):
        print("DM 채널 열기 실패:", dm_data)
        return None
    return dm_data["channel"]["id"]


# ===== 슬랙 실패 알림 =====
def send_failure_notice():
    channel_id = get_dm_channel_id()
    if not channel_id:
        return
    today_str = datetime.now(KST).strftime("%Y년 %m월 %d일")
    requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={
            "channel": channel_id,
            "text": f":warning: {today_str} 시황 메시지를 찾지 못했습니다. 텔레그램 채널을 확인해주세요."
        }
    )
    print("실패 알림 전송 완료")


# ===== 슬랙으로 전송 =====
def send_to_slack(image_bytes, message_count):
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    upload_response = requests.post(
        "https://slack.com/api/files.getUploadURLExternal",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        data={"filename": f"시황정리_{today_str}.png", "length": len(image_bytes)}
    )
    upload_data = upload_response.json()
    if not upload_data.get("ok"):
        print("업로드 URL 발급 실패:", upload_data)
        return

    requests.post(upload_data["upload_url"], data=image_bytes, headers={"Content-Type": "image/png"})

    # 전송 채널 결정
    if TEST_MODE:
        channel_id = get_dm_channel_id()
        if not channel_id:
            return
    else:
        channel_id = SLACK_DAILY_CHANNEL_ID

    requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={
            "files": [{"id": upload_data["file_id"]}],
            "channel_id": channel_id,
            "initial_comment": f"*한국 시장 마감 정리* | {datetime.now(KST).strftime('%Y년 %m월 %d일')} ({message_count}개 메시지)"
        }
    )
    print("슬랙 전송 완료!")


# ===== 메인 =====
async def main():
    print("오늘 이미 전송했는지 확인 중...")
    if already_sent_today():
        print("오늘 이미 전송 완료. 스킵합니다.")
        return

    print("텔레그램 메시지 가져오는 중...")
    messages = await get_today_messages()
    print(f"{len(messages)}개 메시지 발견")

    if not messages:
        print("해당 시간대 메시지가 없습니다.")
        send_failure_notice()
        return

    full_text = "\n\n".join(messages)

    print("\n===== 메시지 원문 =====")
    print(full_text)
    print("========================\n")

    print("텍스트 파싱 중...")
    data = parse_message(full_text)

    print("시장 차트 데이터 가져오는 중...")
    kospi_df, kosdaq_df = get_market_data()
    data['kospi_chart'] = create_chart_base64(kospi_df, data['kospi_class'] == 'up')
    data['kosdaq_chart'] = create_chart_base64(kosdaq_df, data['kosdaq_class'] == 'up')

    print("HTML 렌더링 중...")
    with open("template.html", "r", encoding="utf-8") as f:
        template = f.read()

    html = template
    for key, value in data.items():
        html = html.replace(f"{{{{{key}}}}}", str(value))

    image_bytes = await render_html(html)

    print("슬랙으로 전송 중...")
    send_to_slack(image_bytes, len(messages))


if __name__ == "__main__":
    asyncio.run(main())
