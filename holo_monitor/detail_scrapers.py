from __future__ import annotations
import re
import csv
import os
import json
from typing import Dict, Any, List
from urllib.parse import urlparse
from bs4 import BeautifulSoup

from .runner import fetch_html
from .scrape_utils import g, text_with_breaks, best_from_srcset, abs_url, no_query, uniq, normalize_release_date_jp, normalize_release_date


def _images_from_srcset_or_src(soup: BeautifulSoup, selectors: List[str], base: str) -> List[str]:
    imgs: List[str] = []
    for sel in selectors:
        for im in soup.select(sel):
            ss = im.get('srcset')
            s = im.get('data-src') or im.get('src')
            if ss:
                b = best_from_srcset(ss)
                if b:
                    imgs.append(abs_url(b, base))
            if s:
                imgs.append(abs_url(s, base))
    out = [no_query(u) for u in imgs]
    return uniq(out)



def _body_source(entries):
    data = []
    for source, content in (entries or []):
        text = content if isinstance(content, str) else str(content or '')
        text = g(text)
        if text:
            data.append({'source': source, 'text': text})
    if not data:
        return ''
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return '\n'.join('[{{}}] {{}}'.format(item.get('source',''), item.get('text','')) for item in data)


def scrape_amiami(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    out: Dict[str, Any] = {
        'Title': '', 'Body': '', 'BodySource': '', 'Images': [],
        'Maker': '', 'Materials': '', 'Modeler': '', 'PriceValue': '',
        'PriceCurrency': '', 'PriceTaxIncluded': '', 'ReleaseDate': '',
        'Tags': '', 'Series': '', 'Character': '', 'Copyright': '',
        'overview': '', 'JAN': ''
    }
    t = soup.select_one('#maincontents > div.title_area > h2')
    out['Title'] = g(t.text) if t else ''

    imgs: List[str] = []
    main = soup.select_one('#detail_detail__main_image_area')
    if main and main.has_attr('data-main-image'):
        imgs.append(abs_url(main['data-main-image'], url))
    imgs += _images_from_srcset_or_src(soup, ['#detail_detail__main_image_area img'], url)
    for el in soup.select('#gallery [data-item-image], .gallery_area [data-item-image]'):
        u = el.get('data-item-image')
        if u:
            imgs.append(abs_url(u, url))
    # large-only like JS: keep product main/review and filter thumbnails
    imgs = uniq([no_query(u) for u in imgs])
    large = [u for u in imgs if re.search(r"/images/product/(main|review)/", u, re.I)]
    large = [u for u in large if not re.search(r"rthumb|thumbnail|thumb|blank\.gif|_s\.|_m\.|_small\.|_150|_200|_240", u, re.I)]
    large = [u for u in large if re.search(r"\.(?:jpe?g|png|webp)$", u, re.I)]
    out['Images'] = large or imgs

    # Overview/Notes
    note = soup.select_one('#maincontents > div.sales_overview > dl > dd.remarks, #detail__overview .note, #detail__overview > dl > dd.remarks, #maincontents .note')
    out['overview'] = text_with_breaks(note)

    # Body (spec + detail blocks)
    spec_el = soup.select_one('#detail_detail__item_spec')
    detail_el = soup.select_one('#detail_detail__item_detail')
    parts: List[str] = []
    if spec_el:
        parts.append(text_with_breaks(spec_el))
    if detail_el:
        parts.append(text_with_breaks(detail_el))
    body = '\n'.join([g(s) for s in parts if g(s)])
    out['Body'] = body or (out.get('overview') or '')
    out['BodySource'] = out['Body']

    # Price (AmiAmi price block)
    p_el = soup.select_one('#detail_detail__item_price')
    ptxt = g(p_el.get_text()) if p_el else ''
    if ptxt:
        discount_pattern = re.compile(r'\d+\s*(?:%|' + chr(0xFF05) + r')\s*OFF!?', re.IGNORECASE)
        ptxt_clean = discount_pattern.sub('', ptxt).strip()
        price_value = ''
        yen_symbol = chr(0x5186)
        yen_matches = re.findall(r'([0-9][0-9,]*)' + yen_symbol, ptxt_clean)
        if yen_matches:
            price_value = re.sub(r'[^0-9]', '', yen_matches[0])
        if not price_value:
            numeric_chunks = []
            for chunk in re.findall(r'[0-9][0-9,]*', ptxt_clean):
                digits = re.sub(r'[^0-9]', '', chunk)
                if digits:
                    numeric_chunks.append(int(digits))
            if numeric_chunks:
                price_value = str(max(numeric_chunks))
        if not price_value:
            price_value = re.sub(r'[^0-9]', '', ptxt)
        out['PriceValue'] = price_value
        out['PriceCurrency'] = 'JPY' if re.search(r'JPY|' + yen_symbol, ptxt, re.I) else 'JPY'
        scope = g(ptxt_clean + ' ' + out.get('Body', ''))
        tax_included_marker = chr(0x7A0E) + chr(0x8FBC)
        tax_excluded_marker = chr(0x7A0E) + chr(0x629C)
        out['PriceTaxIncluded'] = 'TRUE' if tax_included_marker in scope else ('FALSE' if tax_excluded_marker in scope else '')
    else:
        # fallback
        meta = soup.select_one('meta[property="og:price:amount"], meta[property="product:price:amount"]')
        if meta and meta.get('content'):
            out['PriceValue'] = re.sub(r'[^0-9]', '', meta.get('content') or '')
            out['PriceCurrency'] = 'JPY'

    # Release date
    r_el = soup.select_one('#maincontents > div.sales_overview > dl > dd.releasedate, #detail_detail__releaseDate, .item_detail_release p.release, .item_status p.release')
    if r_el:
        out['ReleaseDate'] = normalize_release_date_jp(r_el.text)
    if not out.get('ReleaseDate'):
        # Fallback: scan text lines containing '発売' and a year
        raw = soup.get_text('\n')
        cand = ''
        for line in (raw.split('\n') if raw else []):
            s = (line or '').strip()
            if '発売' in s and re.search(r'\d{4}', s):
                cand = s
                break
        if cand:
            val = normalize_release_date_jp(cand)
            if re.match(r'^\d{4}-\d{2}(-\d{2})?$', val):
                out['ReleaseDate'] = val

    # AmiAmi specific sales_overview area
    mk = soup.select_one('#maincontents > div.sales_overview > dl > dd.brand > div > a')
    if mk:
        out['Maker'] = g(mk.get_text())
    ser = soup.select_one('#maincontents > div.sales_overview > dl > dd.seriestitle > div:nth-child(1) > a, #maincontents > div.sales_overview > dl > dd.originaltitle > div:nth-child(1) > a')
    if ser:
        out['Series'] = g(ser.get_text())
    ch = soup.select_one('#maincontents > div.sales_overview > dl > dd.charactername > div > a')
    if ch:
        out['Character'] = g(ch.get_text())
    mdl = soup.select_one('#maincontents > div.sales_overview > dl > dd.modeler')
    if mdl:
        out['Modeler'] = g(mdl.get_text())
    cr = soup.select_one('#maincontents > div.image_area > p.copyright, p.copyright')
    if cr:
        out['Copyright'] = g(cr.get_text())

    # Tags
    tags = []
    for node in soup.select('#explain > div > a:nth-child(1)'):
        text = g(node.get_text())
        if text:
            tags.append(text)
    out['Tags'] = ', '.join(uniq(tags))
    # JAN: prefer JSON-LD gtin13, fallback to spec text near 'JAN'
    try:
        for sc in soup.select('script[type="application/ld+json"]'):
            try:
                import json as _json
                data = _json.loads(sc.get_text() or '{}')
            except Exception:
                continue
            if isinstance(data, dict):
                gt = str(data.get('gtin13') or data.get('gtin') or data.get('sku') or '').strip()
                gt = re.sub(r'[^0-9０-９]', '', gt)
                gt = ''.join(chr(ord(c)-0xFEE0) if '０' <= c <= '９' else c for c in gt)
                if re.fullmatch(r'\d{13}', gt or ''):
                    out['JAN'] = gt
                    break
    except Exception:
        pass
    if not out.get('JAN'):
        scope = soup.select_one('#detail_detail__item_spec, #detail__overview, #maincontents') or soup
        raw = scope.get_text('\n') if scope else ''
        if raw:
            norm = ''.join(chr(ord(c)-0xFEE0) if '０' <= c <= '９' else c for c in raw)
            m = re.search(r'(JAN[^\n\r]{0,50})', norm, flags=re.I)
            seg = m.group(1) if m else ''
            m2 = re.search(r'(?<!\d)(\d{13})(?!\d)', seg)
            if not m2:
                m2 = re.search(r'(?<!\d)(\d{13})(?!\d)', norm)
            if m2:
                out['JAN'] = m2.group(1)
    return out


def scrape_kotobukiya(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    out: Dict[str, Any] = {
        'Title': '', 'Body': '', 'BodySource': '', 'Images': [],
        'Maker': '', 'Materials': '', 'Modeler': '', 'PriceValue': '',
        'PriceCurrency': '', 'PriceTaxIncluded': '', 'ReleaseDate': '', 'JAN': '',
        'Tags': '', 'Series': '', 'Character': '', 'Copyright': '',
        'overview': '', 'PreorderStart': '', 'PreorderEnd': ''
    }

    # Title
    t = soup.select_one('#gallery > div.goodsspec_ > h1')
    out['Title'] = g(t.get_text()) if t else ''

    # Images: 指定セレクタのみ + 大サイズ(L)のみ
    imgs: List[str] = []
    for im in soup.select('#img_gallery li img'):
        s = im.get('src') or im.get('data-original') or im.get('data-src')
        if not s:
            continue
        u = no_query(abs_url(s, url))
        # 大サイズの判定: パスに /img/goods/L/ を含むもののみ
        imgs.append(u)
    out['Images'] = uniq(imgs)

    # Body（overviewは取得しない）
    body_el = soup.select('#disp_fixed > div')
    body_parts: List[str] = []
    for el in body_el:
        text = text_with_breaks(el)
        if g(text):
            body_parts.append(text)
    body_text = '\n'.join([g(p) for p in body_parts if g(p)])
    out['Body'] = body_text
    out['BodySource'] = body_text
    # overviewは混乱回避のため空のまま

    # Price
    val_el = soup.select_one('#spec_price > div.normal_price_ > p > span.price_num_')
    tax_el = soup.select_one('#spec_price > div.normal_price_ > p > span.tax_')
    cur_el = soup.select_one('#spec_price > div.normal_price_ > p > span.price_unit_')
    if val_el:
        out['PriceValue'] = re.sub(r'[^0-9]', '', g(val_el.get_text()))
    # Currency: map 円 -> JPY
    cur_txt = g(cur_el.get_text()) if cur_el else ''
    out['PriceCurrency'] = 'JPY' if cur_txt else 'JPY'
    tax_txt = g(tax_el.get_text()) if tax_el else ''
    if tax_txt:
        out['PriceTaxIncluded'] = 'TRUE' if '税込' in tax_txt else ('FALSE' if '税抜' in tax_txt else '')

    # Release date
    r_el = soup.select_one('#gallery > div.goodsspec_ > div.goods_about > dl.goods_release_ > dd')
    if r_el:
        out['ReleaseDate'] = normalize_release_date_jp(r_el.get_text())

    # Maker / Materials / Copyright
    mk = soup.select_one('body > div.wrapper_ > div.container > div > div.goodsproductdetail_ > div.goods_info.cont1 > div > dl:nth-child(2) > dd > a')
    if mk:
        out['Maker'] = g(mk.get_text())
    mat = soup.select_one('body > div.wrapper_ > div.container > div > div.goodsproductdetail_ > div.goods_info.cont1 > div > dl:nth-child(6) > dd')
    if mat:
        out['Materials'] = g(mat.get_text())
    cr = soup.select_one('body > div.wrapper_ > div.container > div > div.goodsproductdetail_ > dl > dd, p.copyright, .copyright')
    if cr:
        out['Copyright'] = g(cr.get_text())

    # PreorderEnd: 上部予約案内(#spec_goods_comment)から締切日を抽出
    c = soup.select_one('#spec_goods_comment')
    if c and not out.get('PreorderEnd'):
        txt = g(c.get_text(' '))
        val = normalize_release_date_jp(txt)
        if re.match(r'^\d{4}-\d{2}-\d{2}$', val) or re.match(r'^\d{4}-\d{2}$', val):
            out['PreorderEnd'] = val

    # JAN (rare): try 13-digit search
    try:
        raw_all = soup.get_text('\n')
        norm = ''.join(chr(ord(c)-0xFEE0) if '０' <= c <= '９' else c for c in (raw_all or ''))
        m2 = re.search(r'(?<!\d)(\d{13})(?!\d)', norm)
        if m2:
            out['JAN'] = m2.group(1)
    except Exception:
        pass
    return out


def scrape_palverse(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    out: Dict[str, Any] = {
        'Title': '', 'Body': '', 'BodySource': '', 'Images': [],
        'Maker': '', 'Materials': '', 'Modeler': '', 'PriceValue': '',
        'PriceCurrency': '', 'PriceTaxIncluded': '', 'ReleaseDate': '',
        'ShippingDate': '', 'Tags': '', 'Bonus': '', 'overview': '',
        'Copyright': ''
    }

    # Title
    t = soup.select_one('#top > main > div > div.l-in__container > div.l-in__inner > div > div > div > div > div.p-product_detail > div.p-product_detail__ttl')
    out['Title'] = g(t.get_text()) if t else ''

    # Body
    body_el = soup.select_one('#top > main > div > div.l-in__container > div.l-in__inner > div > div > div > div > div.p-product_detail > div.p-product_detail__desc')
    body_text = g(text_with_breaks(body_el)) if body_el else ''
    out['Body'] = body_text
    out['BodySource'] = body_text

    # Images (main slider uses background-image on divs; exclude thumbnails)
    imgs: List[str] = []
    for el in soup.select('.p-product__slide-inner .p-product_slide.js-pro-slide .p-product_slide__img-inner'):
        style = el.get('style') or ''
        m = re.search(r'background-image\s*:\s*url\(([^)]+)\)', style, flags=re.I)
        if not m:
            continue
        u = m.group(1).strip().strip('\"\'')
        if u:
            imgs.append(abs_url(u, url))
    out['Images'] = [no_query(u) for u in uniq(imgs)]

    # PriceValue (digits only) + tax flag
    p_el = soup.select_one('#top > main > div > div.l-in__container > div.l-in__inner > div > div > div > div > div.p-product_detail > dl > dd:nth-child(2) > ul > li')
    if p_el:
        ptxt = g(p_el.get_text(' '))
        out['PriceValue'] = re.sub(r'[^0-9]', '', ptxt)
        out['PriceCurrency'] = 'JPY'
        scope = g((p_el.parent.get_text(' ') if p_el.parent is not None else '') + ' ' + ptxt)
        out['PriceTaxIncluded'] = 'TRUE' if '税込' in scope else ('FALSE' if '税抜' in scope else '')

    # Materials
    m_el = soup.select_one('#top > main > div > div.l-in__container > div.l-in__inner > div > div > div > div > div.p-product_detail > dl > dd:nth-child(4)')
    if m_el:
        out['Materials'] = g(m_el.get_text(' '))

    # ShippingDate (normalize JP date like 2026年3月6日(金)発売予定 → 2026-03-06)
    s_el = soup.select_one('#top > main > div > div.l-in__container > div.l-in__inner > div > div > div > div > div.p-product_detail > dl > dd:nth-child(10)')
    if s_el:
        s_txt = g(s_el.get_text(' '))
        val = normalize_release_date_jp(s_txt)
        if val:
            out['ShippingDate'] = val

    # Copyright
    c_el = soup.select_one('#top > main > div > div.l-in__container > div.l-in__inner > div > div > div > div > div.p-product_detail > div.p-product_detail__copy > small')
    if c_el:
        out['Copyright'] = g(c_el.get_text())

    return out

def scrape_bandai_candy(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    out: Dict[str, Any] = {
        'Title': '', 'Body': '', 'BodySource': '', 'Images': [],
        'PriceValue': '', 'PriceCurrency': '', 'PriceTaxIncluded': '', 'JAN': '',
        'ReleaseDate': '', 'AgeRating': '', 'Copyright': '',
        'overview': '', 'Maker': '', 'Materials': '', 'Modeler': '',
    }

    # Title
    t = soup.select_one('#top > main > article.widthWrapper.marginTop2 > div.flexBlock.flexBetween > div.itemDetailWrapper.flexBlock.flexColumn.flexBetween > h2')
    out['Title'] = g(t.get_text()) if t else ''

    # Body
    body_el = soup.select_one('#top > main > article.widthWrapper.marginTop2 > div.bgWhite.boxRadius.paddingVertical2.paddingHorizontal3.marginTop3 > div')
    body_text = g(text_with_breaks(body_el)) if body_el else ''
    out['Body'] = body_text
    out['BodySource'] = body_text

    # Images (from slider wrapper: try images and background-image styles)
    imgs: List[str] = []
    slider = soup.select_one('#top > main > article.widthWrapper.marginTop2 > div.flexBlock.flexBetween > div.itemSliderWrapper')
    if slider:
        # <img> / srcset
        for im in slider.select('img, source'):
            ss = im.get('srcset')
            s = im.get('data-src') or im.get('src')
            if ss:
                b = best_from_srcset(ss)
                if b:
                    imgs.append(abs_url(b, url))
            if s:
                imgs.append(abs_url(s, url))
        # inline style background-image
        for el in slider.select('[style]'):
            st = el.get('style') or ''
            m = re.search(r'background-image\s*:\s*url\(([^)]+)\)', st, flags=re.I)
            if m:
                u = m.group(1).strip('"\' ')
                if u:
                    imgs.append(abs_url(u, url))
        # anchors to images
        for a in slider.select('a[href]'):
            href = a.get('href') or ''
            if href and re.search(r'\.(?:jpe?g|png|webp)$', href, flags=re.I):
                imgs.append(abs_url(href, url))
    out['Images'] = [no_query(u) for u in uniq(imgs)]

    # Price
    p_el = soup.select_one('#top > main > article.widthWrapper.marginTop2 > div.flexBlock.flexBetween > div.itemDetailWrapper.flexBlock.flexColumn.flexBetween > table > tbody > tr:nth-child(1) > td')
    if p_el:
        ptxt = g(p_el.get_text(' '))
        # 例: 「メーカー希望小売価格：350円（税込385円）」のような場合、最初の数値を採用
        nums = re.findall(r'[0-9][0-9,]*', ptxt)
        if nums:
            out['PriceValue'] = re.sub(r'[^0-9]', '', nums[0])
        else:
            out['PriceValue'] = re.sub(r'[^0-9]', '', ptxt)
        out['PriceCurrency'] = 'JPY'
        out['PriceTaxIncluded'] = 'TRUE' if '税込' in ptxt else ('FALSE' if '税抜' in ptxt else '')

    # Release date
    r_el = soup.select_one('#top > main > article.widthWrapper.marginTop2 > div.flexBlock.flexBetween > div.itemDetailWrapper.flexBlock.flexColumn.flexBetween > table > tbody > tr:nth-child(2) > td')
    if r_el:
        out['ReleaseDate'] = normalize_release_date_jp(r_el.get_text(' '))

    # AgeRating
    a_el = soup.select_one('#top > main > article.widthWrapper.marginTop2 > div.flexBlock.flexBetween > div.itemDetailWrapper.flexBlock.flexColumn.flexBetween > table > tbody > tr:nth-child(4)')
    if a_el:
        out['AgeRating'] = g(a_el.get_text(' '))

    # Copyright
    c_el = soup.select_one('#top > main > article.widthWrapper.marginTop2 > div.bgWhite.boxRadius.paddingVertical2.paddingHorizontal3.marginTop3 > div > p')
    if c_el:
        out['Copyright'] = g(c_el.get_text())

    # JAN (if page provides "JANコード" row)
    try:
        raw_all = soup.get_text('\n')
        norm = ''.join(chr(ord(c)-0xFEE0) if '０' <= c <= '９' else c for c in (raw_all or ''))
        m = re.search(r'(JAN[^\n\r]{0,80})', norm, flags=re.I)
        seg = m.group(1) if m else ''
        m2 = re.search(r'(?<!\d)(\d{13})(?!\d)', seg) or re.search(r'(?<!\d)(\d{13})(?!\d)', norm)
        if m2:
            out['JAN'] = m2.group(1)
    except Exception:
        pass
    return out

def scrape_goodsmile(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    out: Dict[str, Any] = {
        'Title': '', 'Body': '', 'BodySource': '', 'Images': [],
        'Maker': '', 'Materials': '', 'Modeler': '', 'PriceValue': '',
        'PriceCurrency': '', 'PriceTaxIncluded': '', 'ReleaseDate': '', 'JAN': '',
        'ShippingDate': '', 'Tags': '', 'Bonus': '', 'overview': '',
        'Copyright': '', 'PreorderStart': '', 'PreorderEnd': ''
    }

    product_id = None
    m = re.search(r'/product/(\d+)', url)
    if m:
        product_id = m.group(1)

    title_el = soup.select_one('.b-product-info__title, .b-product-info__heading h1, h1')
    if title_el:
        out['Title'] = g(title_el.get_text())
    if not out['Title']:
        meta_title = soup.select_one('meta[property="og:title"]')
        if meta_title and meta_title.get('content'):
            out['Title'] = g(meta_title.get('content'))

    image_nodes = soup.select('.c-photo-product-slider__main img, .c-photo-product-modal__slider img, .c-photo-product-slider__thumbnail img')
    images: List[str] = []
    for node in image_nodes:
        src = node.get('data-src') or node.get('src')
        if not src:
            continue
        full_url = no_query(abs_url(src, url))
        if product_id and product_id not in full_url:
            continue
        images.append(full_url)
    out['Images'] = uniq(images) if images else []

    body_el = soup.select_one('#container > main > div.l-content > article > div > section:nth-child(3) > div > div > div > div')
    if body_el:
        body_text = g(text_with_breaks(body_el))
        out['Body'] = body_text
        out['BodySource'] = body_text

    price_el = soup.select_one('#container > main > div.l-infomation > div > div > div:nth-child(3) > div > p > span')
    if price_el:
        price_text = price_el.get_text(' ')
        out['PriceValue'] = re.sub(r'[^0-9]', '', price_text)
        out['PriceCurrency'] = 'JPY'
        parent_text = g(price_el.parent.get_text(' ')) if price_el.parent is not None else g(price_text)
        if '税込' in parent_text:
            out['PriceTaxIncluded'] = 'TRUE'
        elif '税抜' in parent_text:
            out['PriceTaxIncluded'] = 'FALSE'

    preorder_el = soup.select_one('#status-text-block > p.c-text-body.b-product-info__status')
    if preorder_el:
        preorder_text = g(preorder_el.get_text(' ')).replace('〜', '～').replace('~', '～')
        matches = re.findall(r'(\d{4}年\d{1,2}月\d{1,2}日)', preorder_text)
        if matches:
            out['PreorderStart'] = normalize_release_date_jp(matches[0])
            if len(matches) > 1:
                out['PreorderEnd'] = normalize_release_date_jp(matches[1])

    ship_el = soup.select_one('#status-text-block > p.c-text-body.c-text-body--secondary.b-product-info__note')
    if ship_el:
        ship_text = g(ship_el.get_text(' '))
        m_full = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日)', ship_text)
        if m_full:
            out['ShippingDate'] = normalize_release_date_jp(m_full.group(1))
        else:
            m_month = re.search(r'(\d{4}年\d{1,2}月)', ship_text)
            if m_month:
                out['ShippingDate'] = normalize_release_date_jp(m_month.group(1))

    maker_el = soup.select_one('#specification > dl:nth-child(5) > dd > div > div > a')
    if maker_el:
        out['Maker'] = g(maker_el.get_text())

    copyright_el = soup.select_one('#specification > dl:nth-child(7) > dd > p')
    if copyright_el:
        out['Copyright'] = g(copyright_el.get_text())

    overview_nodes = [
        soup.select_one('#container > main > div.l-content > article > div > section:nth-child(6) > div > div > div:nth-child(1) > ul > li:nth-child(1)'),
        soup.select_one('#purchase-notes > ul > li:nth-child(1)')
    ]
    overview_parts = [g(text_with_breaks(node)) for node in overview_nodes if node]
    out['overview'] = '\n'.join([part for part in overview_parts if part])
    if not out['Body']:
        out['Body'] = out['BodySource'] = out['overview']

    tags = [g(a.get_text()) for a in soup.select('#tags-list li') if g(a.get_text())]
    if tags:
        out['Tags'] = ', '.join(uniq(tags))

    return out
def scrape_hololive(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    out: Dict[str, Any] = {
        'Title': '', 'Body': '', 'BodySource': '', 'Images': [],
        'Maker': '', 'Materials': '', 'Modeler': '', 'PriceValue': '',
        'PriceCurrency': '', 'PriceTaxIncluded': '', 'ReleaseDate': '', 'JAN': '',
        'ShippingDate': '', 'Tags': '', 'Bonus': '', 'overview': ''
    }

    def extract_num(text: Any) -> str:
        candidate = str(text or '')
        match = re.search(r'¥?\s*([0-9][0-9,]{2,})\s*(?:JPY)?', candidate, flags=re.I)
        if match:
            digits = re.sub(r'[^0-9]', '', match.group(1))
            if digits:
                return digits
        if '.' in candidate:
            candidate = candidate.split('.', 1)[0]
        digits = re.sub(r'[^0-9]', '', candidate)
        return digits

    def build_body() -> str:
        desc_el = soup.select_one('.Pdt_description')
        details = soup.select('section.Pdt details, .Pdt details, details')
        parts: List[str] = []
        if desc_el:
            parts.append(text_with_breaks(desc_el))
        goods_detail = None
        for d in details:
            summary = d.select_one('summary')
            if summary and 'グッズ詳細' in g(summary.get_text()):
                goods_detail = d.select_one('.details_inner') or d.select_one('div, .content, .Accordion__Body') or d
                break
        if goods_detail:
            parts.append(text_with_breaks(goods_detail))
        else:
            for d in details:
                c = d.select_one('div, .content, .Accordion__Body') or d
                s = text_with_breaks(c)
                if g(s):
                    parts.append(s)
                    break
        cleaned = [g(p) for p in parts if g(p)]
        return '\n'.join(cleaned)

    title_el = soup.select_one('section.Pdt_heading > h1.Pdt_title, .Pdt_heading > h1.Pdt_title, h1.Pdt_title, section.Pdt_heading > h1, .Pdt_heading > h1, h1')
    if title_el is not None:
        out['Title'] = g(title_el.get_text())
    else:
        meta_title = soup.select_one('meta[property="og:title"]')
        if meta_title and meta_title.get('content'):
            out['Title'] = g(meta_title.get('content'))
    if not out['Title']:
        heading_scope = soup.select_one('section.Pdt_heading')
        if heading_scope:
            candidate = g(heading_scope.get_text(' '))
            if candidate:
                out['Title'] = candidate

    imgs = _images_from_srcset_or_src(
        soup,
        [
            '#swiper-product .swiper-wrapper img',
            '.swiper-product .swiper-wrapper img',
            '.Product__Slideshow .swiper-wrapper img',
            '.swiper-wrapper .swiper-slide img'
        ],
        url,
    )
    filtered = [u for u in imgs if re.search(r'_1024x1024\.(?:jpe?g|png|webp)$', u, re.I)]
    out['Images'] = filtered or imgs

    body_text = build_body()
    out['Body'] = out['BodySource'] = body_text

    price_entries: List[str] = []
    seen: set[str] = set()
    for opt in soup.select('.Pdt_variant .Option, .Pdt_options .Option, .Option'):
        label = opt.select_one('label.ProductOption__label') or opt
        title_node = label.select_one('.Option_title') or opt.select_one('.Option_title') or label
        title = re.sub(r'試聴.*$', '', g(title_node.get_text() if title_node else '')).strip()
        price_node = (
            label.select_one('.prtc_product_option_sp .Option_price .money')
            or label.select_one('.prtc_product_option_flex_box .Option_price .money')
            or label.select_one('.Option_price .money')
            or label.select_one('.money')
            or opt.select_one('.Option_price .money')
            or opt.select_one('.money')
        )
        price_num = extract_num(price_node.get_text() if price_node else '')
        if not title or not price_num:
            continue
        entry = f"{title}：{price_num}"
        if entry in seen:
            continue
        seen.add(entry)
        price_entries.append(entry)
    if price_entries:
        out['PriceValue'] = ', '.join(price_entries)
    else:
        meta_price = soup.select_one('meta[property="og:price:amount"], meta[property="product:price:amount"]')
        pt = meta_price.get('content') if meta_price else ''
        if not pt:
            fallback_price = soup.select_one('.Pdt_price, [data-product-price], .price, .Price-item--regular')
            pt = g(fallback_price.get_text()) if fallback_price else ''
        if pt:
            out['PriceValue'] = extract_num(pt)

    po_text = ''
    po_node = soup.select_one('section.Pdt_shipping p, .Pdt_shipping p')
    if po_node:
        po_text = g(po_node.get_text())
    if po_text:
        matches = re.findall(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', po_text)
        if matches:
            y, mo, d = matches[0]
            out['PreorderStart'] = f"{y}-{int(mo):02d}-{int(d):02d}"
            if len(matches) > 1:
                y, mo, d = matches[1]
                out['PreorderEnd'] = f"{y}-{int(mo):02d}-{int(d):02d}"

    ship_sec = soup.select_one('section.Pdt_shipping, .Pdt_shipping')
    if ship_sec:
        text_scope = str(ship_sec.get_text() or '')
        start_idx = text_scope.find('お届け予定日')
        if start_idx >= 0:
            text_scope = text_scope[start_idx:]
        match = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text_scope)
        if match:
            y, mo, d = match.groups()
            out['ShippingDate'] = f"{y}-{int(mo):02d}-{int(d):02d}"
        else:
            match = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月', text_scope)
            if match:
                y, mo = match.groups()
                out['ShippingDate'] = f"{y}-{int(mo):02d}"

    details_nodes = soup.select('section details, .Pdt details')
    for d in details_nodes:
        summary = d.select_one('summary')
        if summary and '特典' in g(summary.get_text()):
            body_node = d.select_one('div, .content, .Accordion__Body') or d
            out['Bonus'] = text_with_breaks(body_node)
            break
    if not out.get('Bonus') and len(details_nodes) > 1:
        out['Bonus'] = text_with_breaks(details_nodes[1])

    note_el = soup.select_one('#Pdt_note > div, #Pdt_note, .Pdt_note > div')
    out['overview'] = text_with_breaks(note_el)
    return out


def scrape_amazon(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    out: Dict[str, Any] = {
        'Title': '', 'Body': '', 'Images': [], 'PriceValue': '',
        'PriceCurrency': '', 'PriceTaxIncluded': '', 'ReleaseDate': '',
        'ShippingDate': '', 'Copyright': '', 'Maker': '', 'overview': ''
    }
    t = soup.select_one('#productTitle')
    out['Title'] = g(t.text) if t else ''
    # Images (only from #imageBlock_feature_div, prefer largest variants)
    imgs: List[str] = []
    candidates: List[tuple] = []  # (url, area)
    root = soup.select_one('#imageBlock_feature_div')
    for im in (root.select('[data-a-dynamic-image], img, source, span') if root else []):
        s = im.get('data-a-dynamic-image') or im.get('data-old-hires') or im.get('srcset') or im.get('src')
        if not s:
            continue
        # data-a-dynamic-image example: {"https://...jpg":[116,116],"https://...jpg":[500,500]}
        pairs = re.findall(r'(https?://[^"\s]+?\.(?:jpe?g|png|webp))"?\s*:\s*\[(\d+),(\d+)\]', s)
        if pairs:
            for url, w, h in pairs:
                clean = re.sub(r'(\.(?:jpe?g|png|webp)).*$', r'\1', url)
                # allow only jpg/jpeg/png/webp
                if not re.search(r'\.(?:jpe?g|png|webp)$', clean, flags=re.I):
                    continue
                area = int(w) * int(h)
                candidates.append((clean, area))
            continue
        # srcset: pick the best and treat as large-ish (area unknown)
        if im.get('srcset'):
            b = best_from_srcset(im.get('srcset') or '')
            if b:
                clean = re.sub(r'(\.(?:jpe?g|png|webp)).*$', r'\1', b)
                if re.search(r'\.(?:jpe?g|png|webp)$', clean, flags=re.I):
                    candidates.append((clean, 0))
            continue
        # direct URL
        urls = re.findall(r'https?://[^"\s]+?\.(?:jpe?g|png|webp)', s)
        if not urls and s.startswith('http'):
            urls = [s]
        for url in urls:
            clean = re.sub(r'(\.(?:jpe?g|png|webp)).*$', r'\1', url)
            if re.search(r'\.(?:jpe?g|png|webp)$', clean, flags=re.I):
                candidates.append((clean, 0))
    # Sort by area desc, then keep unique
    candidates.sort(key=lambda x: x[1], reverse=True)
    seen = set()
    for url, _ in candidates:
        # ensure allowed extension; drop svg/gif and others
        if not re.search(r'\.(?:jpe?g|png|webp)$', url, flags=re.I):
            continue
        # Exclude small Amazon thumbnails like ...US40_.jpg or ..._99_.png (<=100)
        m_small = re.search(r'([0-9]{1,4})_\.(?:jpe?g|png|webp)$', url, flags=re.I)
        if m_small:
            try:
                if int(m_small.group(1)) <= 100:
                    continue
            except Exception:
                pass
        if url not in seen:
            seen.add(url)
            imgs.append(url)
    out['Images'] = imgs
    p = soup.select_one('.a-price .a-offscreen')
    if p:
        out['PriceValue'] = re.sub(r'[^0-9]', '', g(p.text))
        out['PriceCurrency'] = 'JPY'
    # ReleaseDate (best effort)
    s_el = soup.select_one('#detailBullets_feature_div, #productDetails_detailBullets_sections1, #availability')
    if s_el:
        m = re.search(r'(\d{4})[\/年]\s*(\d{1,2})[\/月]\s*(\d{1,2})', s_el.get_text(' '))
        if m:
            y, mo, d = m.groups()
            out['ReleaseDate'] = f"{y}-{int(mo):02d}-{int(d):02d}"
    # PriceTaxIncluded: prefer explicit message element if present
    tax_el = soup.select_one('#taxInclusiveMessage')
    if tax_el and not out.get('PriceTaxIncluded'):
        txt_tax = g(tax_el.get_text(' '))
        out['PriceTaxIncluded'] = 'TRUE' if re.search(r'税込', txt_tax) else ('FALSE' if re.search(r'税別', txt_tax) else '')
    # Maker from details table (fallbacks)
    if not out.get('Maker'):
        mk = (soup.select_one('#productDetails_expanderTables_depthRightSections > div > div > div > table > tbody > tr:nth-child(3) > td')
              or soup.select_one('#productDetails_expanderTables_depthRightSections > div > div > div > table > tbody > tr:nth-child(1) > th'))
        if mk:
            out['Maker'] = g(mk.get_text())
    # PriceTaxIncluded from explicit message element if not set yet
    if not out.get('PriceTaxIncluded'):
        try:
            tax_el = soup.select_one('#taxInclusiveMessage')
            if tax_el:
                txt_tax = g(tax_el.get_text(' '))
                out['PriceTaxIncluded'] = 'TRUE' if re.search(r'税込', txt_tax) else ('FALSE' if re.search(r'税別', txt_tax) else '')
        except Exception:
            pass
    # Maker from product details table (prefer keys), then specific fallbacks
    if not out.get('Maker'):
        try:
            for tr in soup.select('table.prodDetTable tr'):
                th = tr.select_one('th')
                td = tr.select_one('td')
                if th is None or td is None:
                    continue
                key = g(th.get_text())
                if any(k in key for k in ['メーカー名', 'ブランド名']):
                    out['Maker'] = g(td.get_text())
                    break
        except Exception:
            pass
    if not out.get('Maker'):
        mk_el = (soup.select_one('#productDetails_expanderTables_depthRightSections > div > div > div > table > tbody > tr:nth-child(3) > td')
                 or soup.select_one('#productDetails_expanderTables_depthRightSections > div > div > div > table > tbody > tr:nth-child(1) > th'))
        if mk_el:
            out['Maker'] = g(mk_el.get_text())
    # Overview: append Amazon universal product alert block if present
    try:
        alert_el = soup.select_one('#CardInstancePzztrdhVEnrUWRBD5K49Dw, [data-card-metrics-id="universal-product-alert_DetailPage_0"]')
        if alert_el:
            note_txt = text_with_breaks(alert_el)
            if note_txt:
                out['overview'] = (out.get('overview') + ('\n' if out.get('overview') else '') + g(note_txt))
    except Exception:
        pass
    # Use CSV mapping (hologoods.csv) to fill missing Amazon fields
    try:
        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'hologoods.csv')
        if os.path.exists(csv_path):
            with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                row = next(reader, None)
            if row:
                # Body
                if not out.get('Body'):
                    sel = (row.get('Body') or '').replace('\n', ' ').strip()
                    if sel:
                        parts: List[str] = []
                        for el in soup.select(sel):
                            t = text_with_breaks(el)
                            if t:
                                parts.append(t)
                        if parts:
                            out['Body'] = '\n'.join([g(s) for s in parts if g(s)])
                # PriceValue
                if not out.get('PriceValue'):
                    sel = (row.get('PriceValue') or '').replace('\n', ' ').strip()
                    el = soup.select_one(sel) if sel else None
                    if el:
                        out['PriceValue'] = re.sub(r'[^0-9]', '', g(el.get_text()))
                        if out['PriceValue']:
                            out['PriceCurrency'] = out.get('PriceCurrency') or 'JPY'
                # PriceCurrency
                if not out.get('PriceCurrency'):
                    sel = (row.get('PriceCurrency') or '').replace('\n', ' ').strip()
                    el = soup.select_one(sel) if sel else None
                    if el:
                        sym = g(el.get_text())
                        out['PriceCurrency'] = 'JPY' if ('¥' in sym or '円' in sym or 'JPY' in sym.upper()) else ''
                # PriceTaxIncluded
                if not out.get('PriceTaxIncluded'):
                    sel = (row.get('PriceTaxIncluded') or '').replace('\n', ' ').strip()
                    el = soup.select_one(sel) if sel else None
                    if el:
                        txt = g(el.get_text(' '))
                        out['PriceTaxIncluded'] = 'TRUE' if re.search(r'税込', txt) else ('FALSE' if re.search(r'税別', txt) else '')
                # ShippingDate
                if not out.get('ShippingDate'):
                    sel = (row.get('ShippingDate') or '').replace('\n', ' ').strip()
                    el = soup.select_one(sel) if sel else None
                    if el:
                        val = normalize_release_date_jp(el.get_text(' '))
                        if re.match(r'^\d{4}-\d{2}(-\d{2})?$', val):
                            out['ShippingDate'] = val
                # Copyright
                if not out.get('Copyright'):
                    sel = (row.get('Copyright') or '').replace('\n', ' ').strip()
                    el = soup.select_one(sel) if sel else None
                    if el:
                        out['Copyright'] = g(el.get_text())
    except Exception:
        # Ignore mapping errors and keep defaults
        pass
    # Amazon CSV mapping in JS left many fields empty; keep similar
    out['Tags'] = ''
    out['PriceCurrency'] = out.get('PriceCurrency', '')
    out['PriceTaxIncluded'] = out.get('PriceTaxIncluded', '')
    out['ReleaseDate'] = out.get('ReleaseDate', '')
    return out


def scrape_gamers(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    out: Dict[str, Any] = {
        'Title': '', 'Body': '', 'Images': [], 'Maker': '', 'Materials': '',
        'Modeler': '', 'PriceValue': '', 'PriceCurrency': '', 'PriceTaxIncluded': '',
        'ReleaseDate': '', 'Tags': ''
    }
    t = soup.select_one('h1.ttl_style01.txt_wrap, #item_detail > h1')
    out['Title'] = g(t.text) if t else ''
    imgs = _images_from_srcset_or_src(soup, ['.item_detail_img img, .item_image_selected img, .itemThumbnails img'], url)
    out['Images'] = imgs
    b = soup.select_one('#item_detail > div.item_detail_content > div.item_detail_content_inner.over, .item_detail_content_inner, .item_detail_content, .detail_info')
    out['Body'] = text_with_breaks(b)
    lines = out['Body'].split('\n') if out['Body'] else []
    mat = next((l for l in lines if re.match(r'^仕様', l)), '')
    if mat:
        out['Materials'] = mat.strip()
    mk = next((l for l in lines if re.match(r'^原型(制作|師)', l)), '')
    cl = next((l for l in lines if re.match(r'^彩色', l)), '')
    out['Modeler'] = '\n'.join([x for x in [mk, cl] if x])
    maker = next((l for l in lines if re.match(r'^(発売元|メーカー|販売元|発売・販売元)', l)), '')
    if maker:
        out['Maker'] = g(maker)
    p_wrap = soup.select_one('.item_detail_price')
    p_el = p_wrap.select_one('p.price > span') if p_wrap else None
    if p_el:
        pt = g(p_el.text)
        out['PriceValue'] = re.sub(r'[^0-9]', '', pt)
        out['PriceCurrency'] = 'JPY'
        full = g((p_wrap.get_text(' ') if p_wrap else '') or pt)
        out['PriceTaxIncluded'] = 'TRUE' if '税込' in full else ('FALSE' if '税抜' in full else '')
    r_el = soup.select_one('.item_detail_release p.release')
    if r_el:
        out['ReleaseDate'] = normalize_release_date_jp(r_el.text)
    tag_els = [g(x.text) for x in soup.select('#item_detail section .items_label a') if g(x.text)]
    out['Tags'] = ', '.join(uniq(tag_els))
    return out


def scrape_animate(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    out: Dict[str, Any] = {
        'Title': '', 'Body': '', 'Images': [], 'Maker': '', 'Materials': '',
        'Modeler': '', 'PriceValue': '', 'PriceCurrency': '', 'PriceTaxIncluded': '',
        'ReleaseDate': '', 'Tags': ''
    }
    t = soup.select_one('#container .item_overview_detail > h1')
    out['Title'] = g(t.text) if t else ''
    imgs: List[str] = []
    for el in soup.select('#container .item_images .item_image_selected img, #container .item_images a[href$=".jpg"], #container .item_images a[href$=".png"], #container .item_images a[href$=".webp"]'):
        if el.name == 'img':
            s = el.get('data-src') or el.get('src')
        else:
            s = el.get('href')
        ss = el.get('srcset') if hasattr(el, 'get') else None
        if ss:
            b = best_from_srcset(ss)
            if b:
                imgs.append(abs_url(b, url))
        if s:
            imgs.append(abs_url(s, url))
    out['Images'] = uniq(imgs)
    b = soup.select_one('#item_productinfo > div')
    out['Body'] = text_with_breaks(b)
    scope = soup.select_one('#item_productinfo') or soup
    raw = (scope.get_text('\n') if scope else '')
    maker_line = next((l.strip() for l in raw.splitlines() if re.match(r'^(発売元|メーカー|販売元|発売・販売元)', l.strip())), '')
    if maker_line:
        out['Maker'] = re.sub(r'^(?:発売元|メーカー|販売元|発売・販売元)\s*', '', maker_line).strip()
    p_wrap = soup.select_one('#container .item_overview_detail .item_price')
    p_el = p_wrap.select_one('p.price.new_price, p.price') if p_wrap else None
    if p_el:
        pt = g(p_el.text)
        out['PriceValue'] = re.sub(r'[^0-9]', '', pt)
        out['PriceCurrency'] = 'JPY'
        full = g((p_wrap.get_text(' ') if p_wrap else '') or pt)
        out['PriceTaxIncluded'] = 'TRUE' if '税込' in full else ('FALSE' if '税抜' in full else '')
    r_el = soup.select_one('#container .item_overview_detail .item_status p.release, #container .item_overview_detail .item_status p span')
    if r_el:
        out['ReleaseDate'] = normalize_release_date_jp(r_el.text)
    tag_els = [g(x.text) for x in soup.select('#container .items_label a, #container .items_label span') if g(x.text)]
    out['Tags'] = ', '.join(uniq(tag_els))
    # Overview: capture lines starting with '※' from product info text
    if raw:
        notes = [g((l or '').strip()) for l in raw.splitlines() if (l or '').strip().startswith('※')]
        if notes:
            out['overview'] = '\n'.join([n for n in notes if n])
    # Normalize Maker: drop leading colon-like punctuation remnants (e.g., '：')
    if out.get('Maker'):
        out['Maker'] = re.sub(r'^[：:；]\s*', '', out['Maker'])
    return out


def scrape_gamers(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    out: Dict[str, Any] = {
        'Title': '', 'Body': '', 'BodySource': '', 'Images': [],
        'Maker': '', 'Materials': '', 'Modeler': '',
        'PriceValue': '', 'PriceCurrency': '', 'PriceTaxIncluded': '', 'JAN': '',
        'ReleaseDate': '', 'Tags': '', 'Series': '', 'Character': '',
        'Copyright': '', 'overview': '', 'PreorderStart': '', 'PreorderEnd': ''
    }
    # Title
    t = soup.select_one('h1.ttl_style01.txt_wrap, #item_detail > h1, h1')
    out['Title'] = g(t.text) if t else ''

    # Images (img tags + anchors to full size)
    imgs = _images_from_srcset_or_src(soup, [
        '#item_detail .item_detail_img img',
        '#item_detail .item_image_selected img',
        '#item_detail .itemThumbnails img',
        '.item_detail_img img, .item_image_selected img, .itemThumbnails img',
        '.item_images img, .detail_image img'
    ], url)
    for a in soup.select('#item_detail a[href$=".jpg"], #item_detail a[href$=".png"], #item_detail a[href$=".webp"], a[href$=".jpg"], a[href$=".png"], a[href$=".webp"]'):
        href = a.get('href')
        if href:
            imgs.append(abs_url(href, url))
    out['Images'] = uniq(imgs)

    # Body / overview
    b = soup.select_one('#item_detail > div.item_detail_content > div.item_detail_content_inner.over, #item_detail .item_detail_content_inner, #item_detail .item_detail_content, .detail_info, #item_detail .note, #item_detail .item_overview, .item_overview')
    body = text_with_breaks(b)
    out['Body'] = body
    out['BodySource'] = ''
    out['overview'] = body if body else ''

    # Materials / Modeler / Maker (from body lines heuristics)
    lines = out['Body'].split('\n') if out['Body'] else []
    mat = next((l for l in lines if re.search(r'(素材|材質)\s*[:：]', l)), '')
    if mat:
        out['Materials'] = g(mat)
    mk1 = next((l for l in lines if re.search(r'^原型(制作|師)', l)), '')
    cl1 = next((l for l in lines if re.search(r'^彩色', l)), '')
    if mk1 or cl1:
        out['Modeler'] = '\n'.join([g(x) for x in [mk1, cl1] if x])
    maker_line = next((l for l in lines if re.search(r'^(発売元|メーカー|販売元|発売・販売)', l)), '')
    if maker_line:
        out['Maker'] = g(maker_line)

    # Price
    p_wrap = soup.select_one('.item_detail_price, #item_detail .item_price, .item_price')
    p_el = p_wrap.select_one('p.price > span, .price > span, .price') if p_wrap else None
    if p_el:
        pt = g(p_el.text)
        out['PriceValue'] = re.sub(r'[^0-9]', '', pt)
        out['PriceCurrency'] = 'JPY'
        full = g((p_wrap.get_text(' ') if p_wrap else '') or pt)
        out['PriceTaxIncluded'] = 'TRUE' if '税込' in full else ('FALSE' if '税抜' in full else '')

    # ReleaseDate
    r_el = soup.select_one('.item_detail_release p.release, #item_detail .item_status p.release')
    if r_el:
        out['ReleaseDate'] = normalize_release_date_jp(r_el.text)

    # Copyright
    c_el = soup.select_one('#item_detail p.copyright, p.copyright, .copyright')
    if c_el:
        out['Copyright'] = g(c_el.get_text())
    if not out.get('Copyright'):
        raw = soup.get_text('\n')
        for line in raw.split('\n'):
            s = g(line)
            if s and ('©' in s or '(C)' in s or 'コピーライト' in s):
                out['Copyright'] = s
                break

    # PreorderStart/End (find two JP dates in relevant text)
    scope = soup.select_one('#item_detail') or soup
    txt = scope.get_text('\n') if scope else ''
    m_all = re.findall(r'(\d{4})\s*年\s*(\d{1,2})\s*朁Es*(\d{1,2})\s*日', txt)
    if m_all:
        if len(m_all) > 0:
            y, mo, d = m_all[0]
            out['PreorderStart'] = f"{y}-{int(mo):02d}-{int(d):02d}"
        if len(m_all) > 1:
            y, mo, d = m_all[1]
            out['PreorderEnd'] = f"{y}-{int(mo):02d}-{int(d):02d}"

    # Tags
    tag_els = [g(x.text) for x in soup.select('#item_detail section .items_label a, .items_label a, .items_label span') if g(x.text)]
    out['Tags'] = ', '.join(uniq(tag_els))
    # JAN from page text (label-near first, then global)
    try:
        raw_all = soup.get_text('\n')
        norm = ''.join(chr(ord(c)-0xFEE0) if '０' <= c <= '９' else c for c in (raw_all or ''))
        m = re.search(r'(JAN[^\n\r]{0,50})', norm, flags=re.I)
        seg = m.group(1) if m else ''
        m2 = re.search(r'(?<!\d)(\d{13})(?!\d)', seg) or re.search(r'(?<!\d)(\d{13})(?!\d)', norm)
        if m2:
            out['JAN'] = m2.group(1)
    except Exception:
        pass
    # JAN rarely present; fallback to 13-digit search
    try:
        raw_all = soup.get_text('\n')
        norm = ''.join(chr(ord(c)-0xFEE0) if '０' <= c <= '９' else c for c in (raw_all or ''))
        m2 = re.search(r'(?<!\d)(\d{13})(?!\d)', norm)
        if m2:
            out['JAN'] = m2.group(1)
    except Exception:
        pass
    # JAN: try JSON-LD or 13-digit in text (Shopify may expose barcode in ld+json/variants)
    try:
        import json as _json
        for sc in soup.select('script[type="application/ld+json"]'):
            try:
                data = _json.loads(sc.get_text() or '{}')
            except Exception:
                continue
            if isinstance(data, dict):
                gt = str(data.get('gtin13') or data.get('gtin') or data.get('sku') or '').strip()
                gt = re.sub(r'[^0-9０-９]', '', gt)
                gt = ''.join(chr(ord(c)-0xFEE0) if '０' <= c <= '９' else c for c in gt)
                if re.fullmatch(r'\d{13}', gt or ''):
                    out['JAN'] = gt
                    break
    except Exception:
        pass
    if not out.get('JAN'):
        try:
            raw_all = soup.get_text('\n')
            norm = ''.join(chr(ord(c)-0xFEE0) if '０' <= c <= '９' else c for c in (raw_all or ''))
            m2 = re.search(r'(?<!\d)(\d{13})(?!\d)', norm)
            if m2:
                out['JAN'] = m2.group(1)
        except Exception:
            pass
    return out

def scrape_detail(url: str) -> Dict[str, Any]:
    html = fetch_html(url)
    host = urlparse(url).hostname or ''
    host = host.lower()
    if host.endswith('amiami.jp'):
        return scrape_amiami(url, html)
    if host.endswith('bandai.co.jp') and '/candy/' in url:
        return scrape_bandai_candy(url, html)
    if host.endswith('kotobukiya.co.jp'):
        return scrape_kotobukiya(url, html)
    if host.endswith('palverse-figure.com'):
        return scrape_palverse(url, html)
    if host.endswith('goodsmile.com'):
        return scrape_goodsmile(url, html)
    if host.endswith('shop.hololivepro.com'):
        return scrape_hololive(url, html)
    if host.endswith('amazon.co.jp'):
        return scrape_amazon(url, html)
    if host.endswith('gamers.co.jp'):
        return scrape_gamers2(url, html)
    if host.endswith('animate-onlineshop.jp'):
        return scrape_animate(url, html)
    # Fallback generic: capture title and basic images/text
    soup = BeautifulSoup(html, 'html.parser')
    out: Dict[str, Any] = {'Title': '', 'Body': '', 'Images': []}
    t = soup.select_one('title, h1, h2')
    out['Title'] = g(t.text) if t else ''
    out['Body'] = text_with_breaks(soup.select_one('main, article, body'))
    out['Images'] = _images_from_srcset_or_src(soup, ['img'], url)
    return out


def scrape_gamers2(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    out: Dict[str, Any] = {
        'Title': '', 'Body': '', 'BodySource': '', 'Images': [],
        'Maker': '', 'Materials': '', 'Modeler': '',
        'PriceValue': '', 'PriceCurrency': '', 'PriceTaxIncluded': '', 'JAN': '',
        'ReleaseDate': '', 'Tags': '', 'Series': '', 'Character': '',
        'Copyright': '', 'overview': '', 'PreorderStart': '', 'PreorderEnd': ''
    }
    # Title
    t = soup.select_one('h1.ttl_style01.txt_wrap, #item_detail > h1, h1')
    out['Title'] = g(t.text) if t else ''

    # Images (preserve query parameters for CDN URLs)
    imgs: List[str] = []
    for sel in [
        '#item_detail .item_detail_img img',
        '#item_detail .item_image_selected img',
        '#item_detail .itemThumbnails img',
        '.item_detail_img img, .item_image_selected img, .itemThumbnails img',
        '.item_images img, .detail_image img'
    ]:
        for im in soup.select(sel):
            s = im.get('data-src') or im.get('src')
            if s:
                imgs.append(abs_url(s, url))
            ss = im.get('srcset')
            if ss:
                b = best_from_srcset(ss)
                if b:
                    imgs.append(abs_url(b, url))
    for a in soup.select('#item_detail a[href$=".jpg"], #item_detail a[href$=".png"], #item_detail a[href$=".webp"], a[href$=".jpg"], a[href$=".png"], a[href$=".webp"]'):
        href = a.get('href')
        if href:
            imgs.append(abs_url(href, url))
    out['Images'] = uniq(imgs)

    # Body / overview
    b = soup.select_one('#item_detail > div.item_detail_content > div.item_detail_content_inner.over, #item_detail .item_detail_content_inner, #item_detail .item_detail_content, .detail_info, #item_detail .note, #item_detail .item_overview, .item_overview')
    body = text_with_breaks(b)
    out['Body'] = body
    out['BodySource'] = ''
    out['overview'] = body if body else ''

    # Parse lines for structured fields
    lines = out['Body'].split('\n') if out['Body'] else []
    # Materials: 仕様: ... preferred
    spec_line = next((l for l in lines if re.match(r'^\s*仕様\s*[:：]', l)), '')
    if spec_line:
        out['Materials'] = re.sub(r'^\s*仕様\s*[:：]\s*', '', g(spec_line))
    else:
        mat = next((l for l in lines if re.search(r'(素材|材質)\s*[:：]', l)), '')
        if mat:
            out['Materials'] = re.sub(r'^.*?[:：]\s*', '', g(mat))
    # Modeler
    mk = next((l for l in lines if re.match(r'^原型(制作|師)', l)), '')
    cl = next((l for l in lines if re.match(r'^彩色', l)), '')
    if mk or cl:
        out['Modeler'] = '\n'.join([x for x in [g(mk), g(cl)] if x])
    # Maker
    maker_line = next((l for l in lines if re.search(r'^(発売元|メーカー|販売元|発売・販売)\s*[:：]', l)), '')
    if maker_line:
        out['Maker'] = re.sub(r'^(発売元|メーカー|販売元|発売・販売)\s*[:：]\s*', '', g(maker_line))

    # Price
    p_wrap = soup.select_one('.item_detail_price, #item_detail .item_price, .item_price')
    p_el = p_wrap.select_one('p.price > span, .price > span, .price') if p_wrap else None
    if p_el:
        pt = g(p_el.text)
        out['PriceValue'] = re.sub(r'[^0-9]', '', pt)
        out['PriceCurrency'] = 'JPY'
        full = g((p_wrap.get_text(' ') if p_wrap else '') or pt)
        out['PriceTaxIncluded'] = 'TRUE' if '税込' in full else ('FALSE' if '税抜' in full else '')

    # ReleaseDate
    r_el = soup.select_one('.item_detail_release p.release, #item_detail .item_status p.release')
    if r_el:
        out['ReleaseDate'] = normalize_release_date_jp(r_el.text)

    # Copyright
    c_el = soup.select_one('#item_detail p.copyright, #item_detail .copyright, p.copyright, .copyright')
    if c_el:
        out['Copyright'] = g(c_el.get_text())
    if not out.get('Copyright'):
        raw = soup.get_text('\n')
        for line in raw.split('\n'):
            s = g(line)
            if s and ('©' in s or '(C)' in s or 'コピーライト' in s):
                out['Copyright'] = s
                break

    # PreorderStart/End (robust: handle ranges like "YYYY年..日～YYYY年..日" or single end like "～YYYY年..日 まで")
    scope = soup.select_one('#item_detail') or soup
    txt = scope.get_text('\n') if scope else ''
    dp = r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日'
    # Try explicit range with wave-dash/tilde/dash between two dates
    m = re.search(dp + r"[\s\S]{0,30}[~〜～\-—–][\s\S]{0,30}" + dp, txt)
    if m:
        y1, mo1, d1, y2, mo2, d2 = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6)
        out['PreorderStart'] = f"{y1}-{int(mo1):02d}-{int(d1):02d}"
        out['PreorderEnd'] = f"{y2}-{int(mo2):02d}-{int(d2):02d}"
    else:
        # Label-guided extraction
        label_scope = re.search(r'(予約|受注|受付)[^\n\r]{0,80}', txt)
        seg = label_scope.group(0) if label_scope else txt
        # End-only like "～YYYY年..日 まで"
        m_end = re.search(r'[~〜～][^\n\r]{0,10}' + dp + r'[^\n\r]{0,10}(まで)?', seg)
        if m_end:
            y2, mo2, d2 = m_end.group(1), m_end.group(2), m_end.group(3)
            out['PreorderEnd'] = f"{y2}-{int(mo2):02d}-{int(d2):02d}"
        # Fallback: first two dates in label segment
        if not out.get('PreorderEnd') or not out.get('PreorderStart'):
            m_all = re.findall(dp, seg)
            if m_all:
                if len(m_all) > 0 and not out.get('PreorderStart'):
                    y, mo, d = m_all[0]
                    out['PreorderStart'] = f"{y}-{int(mo):02d}-{int(d):02d}"
                if len(m_all) > 1 and not out.get('PreorderEnd'):
                    y, mo, d = m_all[1]
                    out['PreorderEnd'] = f"{y}-{int(mo):02d}-{int(d):02d}"

    # Tags
    tag_els = [g(x.text) for x in soup.select('#item_detail section .items_label a, .items_label a, .items_label span') if g(x.text)]
    out['Tags'] = ', '.join(uniq(tag_els))
    # JAN from page text
    try:
        raw_all = soup.get_text('\n')
        norm = ''.join(chr(ord(c)-0xFEE0) if '０' <= c <= '９' else c for c in (raw_all or ''))
        m = re.search(r'(JAN[^\n\r]{0,50})', norm, flags=re.I)
        seg = m.group(1) if m else ''
        m2 = re.search(r'(?<!\d)(\d{13})(?!\d)', seg) or re.search(r'(?<!\d)(\d{13})(?!\d)', norm)
        if m2:
            out['JAN'] = m2.group(1)
    except Exception:
        pass
    return out
