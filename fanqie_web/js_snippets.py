"""Browser-side JavaScript snippets for Playwright evaluate() calls."""

BOOKS_JS = r"""() => {
    const results = [];
    const links = document.querySelectorAll('a[href*="chapter-manage/"]');
    for (const link of links) {
        const href = link.getAttribute('href') || '';
        const m = href.match(/chapter-manage\/(\d+)(?:&([^?]*))?/);
        if (!m) continue;
        const bookId = m[1];
        let name;
        if (m[2]) {
            try { name = decodeURIComponent(m[2]); }
            catch { name = m[2]; }
        } else {
            name = '';
        }
        let container = link;
        for (let i = 0; i < 12; i++) {
            if (!container.parentElement) break;
            container = container.parentElement;
            const ct = container.textContent || '';
            if (ct.length > 30 &&
                (ct.includes('万字') || /\d+\s*章/.test(ct))) break;
        }
        const text = container.textContent || '';
        const chapterMatch = text.match(/(\d+)\s*章/);
        const wordMatch = text.match(/([\d.]+)\s*万字/);
        const statusMatch = text.match(/(连载中|已完结)/);
        const signMatch = text.match(/(已签约|未签约)/);
        if (!name) {
            const linkText = link.textContent.trim();
            if (linkText) name = linkText;
            else name = '未命名作品';
        }
        results.push({
            bookId, name,
            chapters: chapterMatch ? chapterMatch[1] : '0',
            words: wordMatch ? wordMatch[1] + '万' : '0',
            status: (statusMatch ? statusMatch[1] : '') +
                    (signMatch ? ' · ' + signMatch[1] : ''),
        });
    }
    return results;
}"""

LAST_PUBLISH_JS = r"""() => {
    const re = /(\d{4}[-/]\d{2}[-/]\d{2})\s+(\d{2}:\d{2})/;
    let best = null, bestKey = '';
    for (const row of document.querySelectorAll('tr')) {
        const cells = row.querySelectorAll('td');
        if (cells.length < 2) continue;
        const m = row.textContent.match(re);
        if (!m) continue;
        const d = m[1].replace(/\//g, '-');
        const t = m[2];
        const key = d + ' ' + t;
        if (key > bestKey) {
            best = {date: d, time: t, chapter: cells[0].textContent.trim()};
            bestKey = key;
        }
    }
    return best;
}"""

DETECT_VOLUMES_JS = r"""async () => {
    const selectEl = document.querySelector(
        '.chapter-select-left .serial-select.byte-select:not(.chapter-status-select)');
    if (!selectEl) return { hasVolumes: false, volumes: [], currentVolume: '' };

    const valueEl = selectEl.querySelector('.byte-select-view-value');
    const currentVolume = valueEl ? valueEl.textContent.trim() : '';

    // 展开下拉读取选项，然后关闭
    selectEl.click();
    await new Promise(r => setTimeout(r, 500));

    const volumes = [];
    for (const opt of document.querySelectorAll(
            '.byte-select-option.chapter-select-option')) {
        volumes.push({
            text: opt.textContent.trim(),
            isActive: opt.classList.contains('byte-select-option-selected'),
        });
    }

    // 关闭下拉
    selectEl.click();
    await new Promise(r => setTimeout(r, 300));

    return { hasVolumes: volumes.length > 1, volumes, currentVolume };
}"""

SELECT_VOLUME_JS = r"""async (targetText) => {
    const selectEl = document.querySelector(
        '.chapter-select-left .serial-select.byte-select:not(.chapter-status-select)');
    if (!selectEl) return false;

    selectEl.click();
    await new Promise(r => setTimeout(r, 500));

    for (const opt of document.querySelectorAll(
            '.byte-select-option.chapter-select-option')) {
        if (opt.textContent.trim() === targetText) {
            opt.click();
            await new Promise(r => setTimeout(r, 1200));

            // 切卷后强制回到第一页，避免继承上一个卷的页码
            const pageOne = [...document.querySelectorAll('li.arco-pagination-item')]
                .find(el => el.textContent.trim() === '1');
            if (pageOne && !pageOne.classList.contains('arco-pagination-item-active')) {
                pageOne.click();
                await new Promise(r => setTimeout(r, 1200));
            }
            return true;
        }
    }

    // 未找到目标卷，关闭下拉
    selectEl.click();
    await new Promise(r => setTimeout(r, 300));
    return false;
}"""

DETECT_EDITOR_VOLUMES_JS = r"""async () => {
    const trigger = document.querySelector('.publish-header-volume-wrap-info-title');
    if (!trigger) return { hasVolumes: false, volumes: [], currentVolume: '' };

    const currentVolumeEl = trigger.querySelector('.publish-header-volume-name');
    const currentVolume = currentVolumeEl ? currentVolumeEl.textContent.trim() : '';

    trigger.click();
    await new Promise(r => setTimeout(r, 500));

    const volumes = [];
    for (const opt of document.querySelectorAll('.editor-volume-list-item')) {
        const txt = opt.textContent.trim();
        if (txt) {
            volumes.push({
                text: txt,
                isActive: opt.querySelector('.selected') !== null || txt === currentVolume,
            });
        }
    }

    const cancelBtn = [...document.querySelectorAll('button')].find(
        el => el.textContent.trim() === '取消'
    );
    if (cancelBtn) cancelBtn.click();
    await new Promise(r => setTimeout(r, 300));

    return { hasVolumes: volumes.length > 1, volumes, currentVolume };
}"""

SELECT_EDITOR_VOLUME_JS = r"""async (targetText) => {
    const trigger = document.querySelector('.publish-header-volume-wrap-info-title');
    if (!trigger) return false;

    trigger.click();
    await new Promise(r => setTimeout(r, 500));

    let matched = false;
    for (const opt of document.querySelectorAll('.editor-volume-list-item')) {
        if (opt.textContent.trim() === targetText) {
            opt.click();
            matched = true;
            break;
        }
    }
    if (!matched) {
        const cancelBtn = [...document.querySelectorAll('button')].find(
            el => el.textContent.trim() === '取消'
        );
        if (cancelBtn) cancelBtn.click();
        await new Promise(r => setTimeout(r, 300));
        return false;
    }

    const confirmBtn = [...document.querySelectorAll('button')].find(
        el => el.textContent.trim() === '确定'
    );
    if (!confirmBtn) return false;
    confirmBtn.click();
    await new Promise(r => setTimeout(r, 1000));
    return true;
}"""

__all__ = [
    "BOOKS_JS",
    "LAST_PUBLISH_JS",
    "DETECT_VOLUMES_JS",
    "SELECT_VOLUME_JS",
    "DETECT_EDITOR_VOLUMES_JS",
    "SELECT_EDITOR_VOLUME_JS",
]
