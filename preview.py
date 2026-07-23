import asyncio
import base64
import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from playwright.async_api import async_playwright

# 샘플 데이터 (어제 메시지 기준)
data = {
    'date': '2026년 07월 23일',
    'kospi_value': '7,096.89', 'kospi_change': '+4.40%', 'kospi_class': 'up', 'kospi_arrow': '▲',
    'kosdaq_value': '790.28', 'kosdaq_change': '+5.22%', 'kosdaq_class': 'up', 'kosdaq_arrow': '▲',
    'kospi_comment': '코스피는 강세 마감. 외국인 중심으로 매수세 유입되며 7,000p 회복.<br>알파벳 AI클라우드 호실적 및 자본지출 계획 상향 조정 영향',
    'kosdaq_comment': '코스닥은 강세 마감. 외국인과 기관 순매수에 급등.<br>바이오와 이차전지, 로봇 중심으로 매수세 확산. 소부장은 상대적으로 부진',
    'issue_text': '알파벳 실적: 구글 클라우드 매출은 248억 달러(+82% YoY) 기록.<br>올해 자본지출은 기존 1,900억 달러에서 2,050억 달러로 확대',
    'foreign_flow': '+21,358억원', 'foreign_class': 'up',
    'individual_flow': '-22,079억원', 'individual_class': 'down',
    'institution_flow': '+976억원', 'institution_class': 'up',
    'sector_text': '건설(+10.47%), IT 서비스(+7.77%), 금속(+7.16%)을 포함한 전 업종 강세',
    'stock_text': 'SK이터닉스(+30.00%)는 유가 급등에 신재생 에너지가 부각되며 상한가.<br>신안우이 해상풍력 사업에 개발·시공사로 참여',
    'exchange_rate': '1,466.8원', 'exchange_change': '-13.3원', 'exchange_class': 'down',
    'bond_rate': '3.917%', 'bond_change': '+0.4bp', 'bond_class': 'up',
    'footnote': 'cf) 은행은 코스피 은행 지수 부재로 KRX 은행 지수 수익률로 대체',
    'kospi_chart': '', 'kosdaq_chart': '',
}

def make_dummy_chart(is_up, seed=42):
    line_color = "#FD2929" if is_up else "#47A5ED"
    np.random.seed(seed)
    # 어제 데이터 (회색)
    prev = 100 + np.cumsum(np.random.randn(80) * 0.3)
    # 오늘 데이터
    today = prev[-1] + np.cumsum(np.random.randn(120) * 0.3)
    if is_up:
        today += np.linspace(0, 3, 120)
    else:
        today -= np.linspace(0, 3, 120)

    baseline = float(today[0])
    total = len(prev) + len(today)
    x_prev = list(range(len(prev)))
    x_today = list(range(len(prev), total))

    fig, ax = plt.subplots(figsize=(3.5, 0.68), dpi=150)
    fig.patch.set_facecolor("#2a2a2a")
    ax.set_facecolor("#2a2a2a")
    ax.plot(x_prev, prev, color="#888888", linewidth=1.2)
    ax.axhline(y=baseline, color="#555", linewidth=0.7, linestyle="--", alpha=0.6)
    ax.plot(x_today, today, color=line_color, linewidth=1.8)
    ax.fill_between(x_today, today, baseline, color=line_color, alpha=0.18)
    ax.set_xlim(0, total - 1)
    ax.axis('off')
    fig.tight_layout(pad=0)

    buf = io.BytesIO()
    fig.savefig(buf, format='PNG', bbox_inches='tight', pad_inches=0, facecolor="#2a2a2a", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')

async def main():
    data['kospi_chart'] = make_dummy_chart(True, seed=42)
    data['kosdaq_chart'] = make_dummy_chart(True, seed=17)

    with open("template.html", "r", encoding="utf-8") as f:
        template = f.read()

    html = template
    for key, value in data.items():
        html = html.replace(f"{{{{{key}}}}}", str(value))

    with open("preview.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("preview.html 저장 완료!")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 800, "height": 1200}, device_scale_factor=2)
        await page.set_content(html, wait_until="networkidle")
        height = await page.evaluate("document.body.scrollHeight")
        await page.set_viewport_size({"width": 800, "height": height})
        await page.screenshot(full_page=True, path="preview.png")
        await browser.close()

    print("preview.png 저장 완료! open preview.png 으로 확인하세요.")

asyncio.run(main())
