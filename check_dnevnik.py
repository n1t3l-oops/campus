import asyncio
import base64
import ctypes
import json
import logging
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
PROFILE = BASE_DIR / "dnevnik_profile"
DATA_FILE = BASE_DIR / "schedule.json"
LOG_FILE = BASE_DIR / "monitor.log"
ERROR_STATE_FILE = BASE_DIR / "last_error.txt"

BASE_URL = (
    "https://schools.dnevnik.ru/v2/r/omsk/schedules/view"
    "?school=53237&group=2509016193679422543"
)
GITHUB_REPO = "n1t3l-oops/campus"
GITHUB_BRANCH = "main"
GITHUB_FILE = "schedule.json"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    encoding="utf-8",
)


def date_from_url(url):
    query = parse_qs(urlparse(url).query)
    try:
        return datetime(
            int(query["year"][0]),
            int(query["month"][0]),
            int(query["day"][0]),
        ).date()
    except (KeyError, ValueError, IndexError):
        return None


def make_week_url(base_url, day):
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query)
    query.update({
        "year": [str(day.year)],
        "month": [str(day.month)],
        "day": [str(day.day)],
    })
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


async def get_week_start(page):
    dates = await page.locator("table.scheduleWeekEditorParent td[id^='d']").evaluate_all(
        """els => els.map(el => {
            const m = (el.id || '').match(/^d(\\d{8})_\\d+$/);
            return m ? m[1] : null;
        }).filter(Boolean)"""
    )
    parsed = []
    for value in dates:
        try:
            parsed.append(datetime.strptime(value, "%Y%m%d").date())
        except ValueError:
            pass
    if not parsed:
        raise RuntimeError("Не удалось определить даты недели в таблице Дневник.ру.")
    return min(parsed)


async def get_week_urls(page):
    current_url = page.url
    week_start = await get_week_start(page)

    links = await page.locator(
        "a[href*='period='][href*='year='][href*='month='][href*='day=']"
    ).evaluate_all("els => els.map(a => a.href)")

    dated_links = {}
    for url in links:
        query = parse_qs(urlparse(url).query)
        try:
            day = date(
                int(query["year"][0]),
                int(query["month"][0]),
                int(query["day"][0]),
            )
        except (KeyError, ValueError, TypeError):
            continue
        dated_links[day] = url

    previous = [
        (day, url) for day, url in dated_links.items()
        if 0 < (week_start - day).days <= 9
    ]
    following = [
        (day, url) for day, url in dated_links.items()
        if 0 < (day - week_start).days <= 9
    ]

    if not previous or not following:
        raise RuntimeError(
            "Не удалось найти ссылки на предыдущую и следующую недели "
            "в навигации Дневник.ру."
        )

    previous_url = max(previous, key=lambda item: item[0])[1]
    next_url = min(following, key=lambda item: item[0])[1]
    return [previous_url, current_url, next_url]


async def extract_page(page):
    return await page.locator("table.scheduleWeekEditorParent div.dL").evaluate_all(
        """els => els.map(el => {
            const td = el.closest('td[id^="d"]');
            const cell = td?.id?.match(/^d(\\d{8})_(\\d+)$/);
            const subject = el.querySelector('a.aL');
            const teacher = el.querySelector('[data-test-id="teacher"]');
            const time = el.querySelector('[data-test-id="time"]');
            const room = el.querySelector('[data-test-id="room"]');
            const timeText = time?.title || time?.innerText || '';
            const tm = timeText.match(/(\\d{1,2}:\\d{2})\\s*-\\s*(\\d{1,2}:\\d{2})/);
            const statusClass = ['dLC', 'dLO', 'dLP'].find(c => el.classList.contains(c)) || 'dLP';
            const status = statusClass === 'dLC' ? 'cancelled'
                : statusClass === 'dLO' ? 'changed' : 'normal';
            const siblings = td ? [...td.querySelectorAll(':scope > .dL')] : [];
            return {
                id: (el.id || '').replace(/^l/, ''),
                date: cell ? cell[1] : '',
                pair: cell ? Number(cell[2]) : 0,
                position: siblings.indexOf(el) + 1,
                subgroup: siblings.length > 1 ? siblings.indexOf(el) + 1 : 0,
                status,
                status_class: statusClass,
                subject: subject?.title || subject?.innerText?.trim() || 'Занятие',
                teacher: (teacher?.title || teacher?.innerText || '').replace(/^Учитель:\\s*/, '').trim(),
                room: (room?.title || room?.innerText || '').replace(/^Место проведения:\\s*/, '').trim(),
                start: tm ? tm[1] : '',
                end: tm ? tm[2] : ''
            };
        })"""
    )


async def scrape():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            channel="msedge",
            headless=True,
            viewport={"width": 1440, "height": 1000},
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=90000)
            await page.locator("table.scheduleWeekEditorParent").wait_for(timeout=90000)

            week_urls = await get_week_urls(page)
            current_week_start = await get_week_start(page)
            all_lessons = {}

            for index, url in enumerate(week_urls):
                expected_start = current_week_start + timedelta(days=(index - 1) * 7)
                if index:
                    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
                    await page.locator("table.scheduleWeekEditorParent").wait_for(timeout=90000)
                    expected_key = expected_start.strftime("%Y%m%d")
                    await page.wait_for_function(
                        """expected => {
                            const ids = [...document.querySelectorAll(
                                'table.scheduleWeekEditorParent td[id^="d"]'
                            )].map(td => (td.id || '').match(/^d(\\d{8})_\\d+$/))
                              .filter(Boolean).map(match => match[1]);
                            return ids.length > 0 && ids.includes(expected);
                        }""",
                        arg=expected_key,
                        timeout=30000,
                    )

                actual_start = await get_week_start(page)
                if actual_start != expected_start:
                    raise RuntimeError(
                        f"Дневник.ру открыл не ту неделю: ожидалась {expected_start}, "
                        f"получена {actual_start}. Расписание не опубликовано."
                    )
                lessons = await extract_page(page)
                for lesson in lessons:
                    if not lesson["id"] or not lesson["date"] or not lesson["pair"]:
                        continue
                    all_lessons[lesson["id"]] = lesson

                logging.info(
                    "Неделя %s: извлечено %s занятий",
                    index + 1,
                    len(lessons),
                )

            result = {
                "source": "Dnevnik.ru",
                "group": "БП314",
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "weeks": week_urls,
                "lessons": sorted(
                    all_lessons.values(),
                    key=lambda x: (x["date"], x["pair"], x["position"], x["id"]),
                ),
            }

            if len(result["lessons"]) < 5:
                raise RuntimeError(
                    f"Получено подозрительно мало занятий: {len(result['lessons'])}. "
                    "Возможно, истекла авторизация Дневник.ру."
                )

            temp_file = DATA_FILE.with_suffix(".json.tmp")
            temp_file.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_file.replace(DATA_FILE)

            logging.info(
                "Расписание сохранено: %s занятий за три недели",
                len(result["lessons"]),
            )
            return result
        finally:
            await context.close()


def publish_to_github():
    if not shutil_which("gh"):
        raise RuntimeError("GitHub CLI (gh) не найден. Установите его и выполните gh auth login.")

    endpoint = f"repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    local_bytes = DATA_FILE.read_bytes()

    remote = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )

    sha = None
    if remote.returncode == 0:
        remote_file = json.loads(remote.stdout)
        sha = remote_file.get("sha")
        remote_bytes = base64.b64decode(remote_file.get("content", ""))
        if remote_bytes == local_bytes:
            logging.info("GitHub: расписание уже актуально.")
            return
    elif "404" not in (remote.stderr + remote.stdout):
        raise RuntimeError("GitHub GET error: " + remote.stderr.strip())

    payload = {
        "message": "Update Dnevnik.ru timetable",
        "content": base64.b64encode(local_bytes).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    result = subprocess.run(
        ["gh", "api", endpoint, "--method", "PUT", "--input", "-"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError("GitHub PUT error: " + result.stderr.strip())

    logging.info("GitHub: расписание опубликовано.")


def shutil_which(command):
    import shutil
    return shutil.which(command)


async def main():
    await scrape()
    publish_to_github()
    ERROR_STATE_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        logging.exception("Ошибка обновления расписания")
        message = f"{type(exc).__name__}: {exc}"
        try:
            previous = ERROR_STATE_FILE.read_text(encoding="utf-8") if ERROR_STATE_FILE.exists() else ""
            ERROR_STATE_FILE.write_text(message, encoding="utf-8")
            if previous != message:
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "Не удалось обновить расписание Campus.\n\n"
                    + message
                    + "\n\nПодробности: monitor.log",
                    "Campus — ошибка синхронизации",
                    0x10,
                )
        except Exception:
            logging.exception("Не удалось показать уведомление об ошибке")
        sys.exit(1)
