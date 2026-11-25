"""Buy Wizard101 Bazaar reagents from the foreground game window."""
import ctypes as ct
from ctypes import wintypes
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from itertools import product
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageGrab

HERE = Path(__file__).resolve().parent
RESAMPLE = Image.Resampling.LANCZOS
MARKERS = ((861, 294, 893, 322), (1144, 226, 1252, 250), (578, 243, 629, 265))
TAB = (877, 308)
REAGENT_TABS = ((658,308),(713,308),(768,308),(822,308),TAB,(932,308),(987,308))
NEXT, PREVIOUS = (1166, 750), (1009, 750)
PAGE = (1035, 734, 1140, 764)
NAMEPLATE = (574, 353, 793, 387)
NAME_COLUMN = (877, 381, 1162, 712)
MORE_BUTTON, BUY_BUTTON = (613, 712, 753, 733), (614, 749, 753, 770)
DIALOG_NAME = (811, 389, 1109, 423)
DIALOG_COUNT = (798, 600, 880, 627)
DIALOG_TITLE = (889, 287, 1031, 320)
DIALOG_BUY = (813, 746, 889, 777)
WALLET = (683, 810, 827, 837)
DIALOG_MAX_UNIT = (1014, 491, 1125, 522)
CANCEL = (1073, 761)
MAX_SLIDER, PLUS, SUBMIT = (1085, 614), (1109, 614), (850, 761)


class Stop(Exception):
    pass


def font_bytes(game_dir):
    with (game_dir / 'Data/GameData/_Shared-WorldData.wad').open('rb') as f:
        magic, version, count = struct.unpack('<5sII', f.read(13))
        if magic != b'KIWAD' or version != 1:
            raise Stop('Unrecognized game asset format.')
        for _ in range(count):
            offset, size, packed, compressed, _, length = struct.unpack('<IIIBII', f.read(21))
            if f.read(length).rstrip(b'\0') == b'WizardFancy.ttf':
                f.seek(offset)
                data = f.read(packed if compressed else size)
                return zlib.decompress(data) if compressed else data
    raise Stop('Wizard Fancy font was missing from the installed game.')


def ink(im, color):
    r, g, b = np.asarray(im.convert('RGB')).transpose(2, 0, 1)
    if color == 'disabled':
        rgb = np.asarray(im.convert('RGB'))
        return (rgb.min(axis=2)>115) & (np.ptp(rgb,axis=2)<12)
    if color == 'dialog_yellow':
        # Equal red and green separates the lettering from the gold button border.
        return (np.abs(r.astype('int16')-g)<7) & (g>145) & (b<15)
    if color == 'yellow':
        return (r > 165) & (g > 155) & (b < 100)
    if color == 'white':
        return (r > 190) & (g > 190) & (b > 190)
    return (r < 65) & (g < 65) & (b < 65)


def trim(a):
    ys, xs = np.nonzero(a)
    if not xs.size:
        return None
    return a[ys.min():ys.max()+1, xs.min():xs.max()+1]


def similarity(a, b):
    a = np.ravel(a).astype(float)
    b = np.ravel(b).astype(float)
    return float(a @ b / max(1e-9, np.linalg.norm(a) * np.linalg.norm(b)))


def read_words(image):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)/'field.png'
        image.save(path)
        try:
            result = subprocess.run(['powershell.exe','-NoProfile','-File',
                                     str(HERE/'read-screen.ps1'),str(path)],
                                    capture_output=True,timeout=12,
                                    creationflags=subprocess.CREATE_NO_WINDOW,check=True)
            return json.loads(result.stdout.decode('utf-8-sig'))
        except (ValueError, OSError, subprocess.SubprocessError) as e:
            raise Stop('Windows OCR is unavailable. Check the installed Windows OCR language.') from e


def read_ink(bits):
    im = Image.fromarray(np.where(bits,0,255).astype('uint8'))
    return read_words(im.resize((im.width*3,im.height*3),RESAMPLE))


def normalized(text):
    return ' '.join(text.casefold().split())


def text_score(bits, reference, local=False):
    if bits is None:
        return 0.
    h, w = bits.shape
    aspect = (w / h) / (reference.width / reference.height)
    if not 0.78 <= aspect <= 1.25:
        return 0.
    pred = np.asarray(reference.resize((w, h), RESAMPLE), dtype=float) / 255.
    score = similarity(pred, bits)
    if local:
        bands = max(1, round(reference.width / reference.height / 1.35))
        for x, y in zip(np.array_split(pred, bands, axis=1), np.array_split(bits, bands, axis=1)):
            if x.sum() > 2 and similarity(x, y) < .70:
                return 0.
    return score


class Reader:
    def __init__(self, data):
        self.font = ImageFont.truetype(BytesIO(data), 72)
        with Image.open(HERE / 'bazaar-dialog-title.png') as title:
            self.dialog_title = title.copy()
        with Image.open(HERE / 'bazaar-reference.png') as atlas:
            # Crop inside the icon so the selected tab's border does not affect calibration.
            self.markers = [atlas.crop(box).convert('L') for box in
                            ((15,16,47,44),(0,60,108,84),(0,85,51,107))]
            relic = trim(ink(atlas.crop((0,109,285,135)), 'yellow'))
            self.relic = Image.fromarray(relic.astype('uint8') * 255)

    @lru_cache(maxsize=128)
    def render(self, text):
        box = self.font.getbbox(text)
        im = Image.new('L', (box[2]-box[0]+4, box[3]-box[1]+4))
        ImageDraw.Draw(im).text((2-box[0],2-box[1]), text, fill=255, font=self.font)
        return im.crop(im.getbbox())

    def matches(self, im, text, color='yellow', *, item=False):
        bits = trim(ink(im, color))
        refs = [self.render(text)]
        if item and text.title() != text:
            refs.append(self.render(text.title()))
        if item and text.casefold() == 'outsider relic':
            refs.append(self.relic)
        if not item:
            refs.append(self.render(text.upper()))
        return any(text_score(bits, ref, item) >= (.86 if item else .82) for ref in refs)

    def digits(self, im, color='yellow', page=False):
        bits = trim(ink(im, color))
        if bits is None:
            return ''
        cols = np.r_[False, bits.any(axis=0), False].astype(int)
        edges = np.flatnonzero(np.diff(cols))
        result = ''
        for x0, x1 in edges.reshape(-1, 2):
            glyph = trim(bits[:, x0:x1])
            scores = sorted(((text_score(glyph, self.render(c)), c)
                             for c in ('0123456789/' if page else '0123456789')), reverse=True)
            if scores[0][0] < .79 or scores[0][0] - scores[1][0] < .018:
                return ''
            result += scores[0][1]
        return result

    def amount(self, image, label, color='dialog_yellow'):
        digits = self.digits(image,color)
        if not digits:
            lines = read_ink(ink(image,color))
            digits = lines[0]['text'].strip() if len(lines)==1 else ''
        if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)',digits):
            raise Stop(f'Could not read {label}.')
        return int(digits.replace(',',''))


@dataclass(frozen=True)
class Layout:
    scale: float
    dx: float
    dy: float

    def point(self, x, y):
        return round(self.dx+x*self.scale), round(self.dy+y*self.scale)

    def crop(self, frame, rect):
        x1,y1,x2,y2 = rect
        return frame.crop((*self.point(x1,y1), *self.point(x2,y2)))


def gray(im):
    return np.asarray(im.convert('L'), dtype=float)


def locate(frame, template, center, scale, radius):
    """Normalized correlation in a small search region around a known landmark."""
    w,h = (max(4,round(i*scale)) for i in template.size)
    x = round(center[0]-w/2)-radius
    y = round(center[1]-h/2)-radius
    area = gray(frame.crop((x,y,x+w+2*radius,y+h+2*radius)))
    target = gray(template.resize((w,h), RESAMPLE))
    target -= target.mean()
    fft = np.fft.rfft2
    corr = np.fft.irfft2(fft(area)*fft(target,s=area.shape,axes=(0,1)).conj(),
                         s=area.shape,axes=(0,1))[:2*radius+1,:2*radius+1]
    def sums(a):
        ii = np.pad(a, ((1,0),(1,0))).cumsum(axis=0).cumsum(axis=1)
        return ii[h:,w:]-ii[:-h,w:]-ii[h:,:-w]+ii[:-h,:-w]
    energy = np.maximum(1, sums(area**2)-sums(area)**2/(w*h))
    scores = corr / (np.sqrt(energy)*max(1,np.linalg.norm(target)))
    iy,ix = np.unravel_index(scores.argmax(), scores.shape)
    return scores[iy,ix], (x+ix+w/2,y+iy+h/2)


def calibrate(frame, cursor, reader):
    centers = np.array([((a+c)/2,(b+d)/2) for a,b,c,d in MARKERS])
    candidates = []
    for scale,(tab_x,_) in product(np.arange(.55, 2.06, .025), REAGENT_TABS):
        predicted_icon = (cursor[0]+(TAB[0]-tab_x)*scale,cursor[1])
        score, pos = locate(frame, reader.markers[0], predicted_icon, scale, round(18*scale))
        if score < .65:
            continue
        pts = [pos]
        scores = [score]
        for i in (1,2):
            predicted = np.array(pos)+(centers[i]-centers[0])*scale
            value, point = locate(frame, reader.markers[i], predicted, scale, round(10*scale)+2)
            scores.append(value)
            pts.append(point)
        if min(scores) > .72:
            candidates.append((sum(scores), np.array(pts)))
    if not candidates:
        raise Stop('Could not recognize the Bazaar. Open Buy > Reagents and hover the center of the category icon you want to watch.')
    pts = max(candidates,key=lambda c:c[0])[1]
    offsets = centers-centers.mean(axis=0)
    scale = (offsets*(pts-pts.mean(axis=0))).sum()/(offsets**2).sum()
    dx,dy = (pts-centers*scale).mean(axis=0)
    if np.abs(centers*scale+[dx,dy]-pts).max() > 4*scale:
        raise Stop('Bazaar landmarks did not align.')
    return Layout(scale,dx,dy)


class Windows:
    def __init__(self):
        self.ui = ct.WinDLL('user32', use_last_error=True)
        self.kernel = ct.WinDLL('kernel32', use_last_error=True)
        self.ui.GetForegroundWindow.restype = wintypes.HWND
        self.ui.WindowFromPoint.argtypes = [wintypes.POINT]
        self.ui.WindowFromPoint.restype = wintypes.HWND
        self.ui.GetAncestor.argtypes = [wintypes.HWND,wintypes.UINT]
        self.ui.GetAncestor.restype = wintypes.HWND
        self.ui.GetClientRect.argtypes = [wintypes.HWND,ct.POINTER(wintypes.RECT)]
        self.ui.ClientToScreen.argtypes = [wintypes.HWND,ct.POINTER(wintypes.POINT)]
        self.ui.GetWindowThreadProcessId.argtypes = [wintypes.HWND,ct.POINTER(wintypes.DWORD)]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ct.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.ui.SetProcessDPIAware()
        self.hwnd = self.ui.GetForegroundWindow()
        pid = wintypes.DWORD()
        self.ui.GetWindowThreadProcessId(self.hwnd,ct.byref(pid))
        proc = self.kernel.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        buf = ct.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buf))
        try:
            if not proc or not self.kernel.QueryFullProcessImageNameW(proc,0,buf,ct.byref(length)):
                raise Stop('Could not identify the foreground window.')
        finally:
            if proc:
                self.kernel.CloseHandle(proc)
        exe = Path(buf.value)
        if exe.name.casefold() != 'wizardgraphicalclient.exe':
            raise Stop('Wizard101 must be the active window when you press F8.')
        self.game_dir = exe.parent.parent
        self.bounds = self.client_bounds()
        self.layout = None

    def client_bounds(self):
        rect, pt = wintypes.RECT(), wintypes.POINT()
        if not self.ui.GetClientRect(self.hwnd,ct.byref(rect)) or not self.ui.ClientToScreen(self.hwnd,ct.byref(pt)):
            raise Stop('The game window closed.')
        return pt.x,pt.y,pt.x+rect.right,pt.y+rect.bottom

    def check(self):
        if self.ui.GetAsyncKeyState(0x79) & 0x8001:
            raise Stop('Stopped with F10.')
        if self.ui.GetForegroundWindow() != self.hwnd or self.client_bounds() != self.bounds:
            raise Stop('Wizard101 lost focus, moved, or resized.')

    def wait(self, seconds):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            self.check()
            time.sleep(.04)

    def screen(self):
        self.check()
        return ImageGrab.grab(bbox=self.bounds, all_screens=True).convert('RGB')

    def click(self, pos, *, client_coords=False):
        self.check()
        x,y = pos if client_coords else self.layout.point(*pos)
        x += self.bounds[0]
        y += self.bounds[1]
        hwnd = self.ui.WindowFromPoint(wintypes.POINT(x,y))
        if self.ui.GetAncestor(hwnd,2) != self.hwnd:
            raise Stop('Another window is covering the game control.')
        self.ui.SetCursorPos(x,y)
        self.wait(.06)
        self.ui.mouse_event(2,0,0,0,0)
        try:
            time.sleep(.045)
        finally:
            self.ui.mouse_event(4,0,0,0,0)
        self.wait(.12)
        self.ui.SetCursorPos(self.bounds[0]+40,self.bounds[1]+110)


class Bazaar:
    def __init__(self, ui, reader, name, scan_only, tab=TAB):
        self.ui, self.reader, self.name, self.scan_only = ui, reader, name, scan_only
        self.layout = ui.layout
        self.tab = tab
        self.last_listing = None
        self.single_next = False
        self.unchanged = 0
        self.last_names = None
        self.ocr_row = None
        self.preview_ink = None
        self.preview_matches = False

    def matches_name(self, image, color):
        if self.reader.matches(image,self.name,color,item=True):
            return True
        lines = read_ink(ink(image,color))
        return len(lines)==1 and normalized(lines[0]['text'])==normalized(self.name)

    def field(self, frame, rect):
        return self.layout.crop(frame,rect)

    def shop(self, frame):
        for rect, ref in zip(MARKERS,self.reader.markers):
            visible = gray(self.field(frame,rect).resize(ref.size, RESAMPLE))
            expected = gray(ref)
            if similarity(visible-visible.mean(),expected-expected.mean()) < .64:
                return False
        return True

    def page(self, frame):
        if not self.shop(frame):
            return None
        indicator = self.field(frame,PAGE)
        if not ink(indicator,'black').any():
            # An empty category hides the counter and can leave a stale name on the left.
            if (self.reader.matches(self.field(frame,BUY_BUTTON),'Buy','disabled')
                    and not ink(self.field(frame,NAME_COLUMN),'dialog_yellow').any()):
                return 0,0
            return None
        text = self.reader.digits(indicator,'black',True)
        m = re.fullmatch(r'(\d{1,3})/(\d{1,3})',text)
        if m:
            cur,total = map(int,m.groups())
            if (cur,total) in ((0,0),(0,1),(1,0)):
                return 0,0
            if 1 <= cur <= total:
                return cur,total
        return None

    def listing(self, timeout=4):
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            frame = self.ui.screen()
            page = self.page(frame)
            if page is not None:
                return frame,page
            self.ui.wait(.12)
        raise Stop('Could not read the Bazaar page indicator. Check for a popup or a covered window.')

    def turn(self, point, expected):
        self.ui.click(point)
        end = time.monotonic()+3
        while time.monotonic()<end:
            frame,page = self.listing()
            if page[0]==expected or page[1]<expected:
                return frame,page
            self.ui.wait(.12)
        raise Stop('The page number did not advance as expected.')

    def find(self, frame):
        for i in range(9):
            y=round(396+37.8*i)
            if self.reader.matches(self.field(frame,(877,y-13,1162,y+14)),self.name,item=True):
                return y
        rect = NAME_COLUMN
        bits = ink(self.field(frame,rect),'yellow')
        if self.last_names is None or not np.array_equal(bits,self.last_names):
            self.ocr_row = None
            for line in read_ink(bits) if bits.any() else ():
                if normalized(line['text'])==normalized(self.name):
                    center = rect[1]+(line['y']+line['h']/2)/(3*self.layout.scale)
                    index = round((center-396)/37.8)
                    if 0<=index<9 and abs(396+37.8*index-center)<12:
                        self.ocr_row = round(396+37.8*index)
                        break
            self.last_names = bits
        return self.ocr_row

    def preview(self, frame, require_buy=True):
        if not self.reader.matches(self.field(frame,DIALOG_TITLE),'Buy More','black'):
            return False
        if require_buy and not self.reader.matches(self.field(frame,DIALOG_BUY),'Buy','dialog_yellow'):
            return False
        label = self.field(frame,DIALOG_NAME)
        bits = ink(label,'yellow')
        if self.preview_ink is None or not np.array_equal(bits,self.preview_ink):
            self.preview_matches = bool(bits.any()) and self.matches_name(label,'yellow')
            self.preview_ink = bits
        return self.preview_matches

    def quantity(self, frame):
        return self.reader.digits(self.field(frame,DIALOG_COUNT),'dialog_yellow')

    def notice_bounds(self, frame):
        # The centered confirmation leaves the Bazaar tabs and page counter visible behind it.
        scale = self.layout.scale
        score,(x,y) = locate(frame,self.reader.dialog_title,self.layout.point(960,460),
                             scale,round(145*scale))
        if score >= .78:
            return (round(x-226*scale),round(y-24*scale),
                    round(x+226*scale),round(y+176*scale))
        return None

    def confirm_notice(self, frame, allow_confirm=True, bounds=None):
        a,b,c,d = bounds or (*self.layout.point(580,285),*self.layout.point(1340,795))
        region = frame.crop((a,b,c,d))
        lines = read_words(region.resize((region.width*2,region.height*2), RESAMPLE))
        body = ' '.join(str(l['text']) for l in lines).casefold()
        body = ' '.join(body.split())
        name = re.escape(self.name.casefold())
        confirm = re.search(r'\bare you sure you want to buy (?:the )?'+name+r'(?: for [\d,]+ gold)?\s*\?', body)
        sold = 'this item has already sold' in body
        funds = any(text in body for text in ('not enough gold','insufficient gold',
                    'do not have enough gold',"don't have enough gold",'cannot afford'))
        if confirm:
            if not allow_confirm:
                return 'waiting'
            labels = ('yes','buy')
        elif sold or funds or re.search(r'\b(?:transaction successful|purchase successful)\b',body):
            labels = ('ok','okay')
        else:
            raise Stop('Unrecognized game dialog. Left it open for you.')
        buttons = []
        for line in lines:
            words = line.get('words') or [line]
            if all(normalized(w['text']) in ('yes','no','buy','cancel','ok','okay') for w in words):
                buttons.extend(w for w in words if normalized(w['text']) in labels)
        if len(buttons)!=1:
            raise Stop('Could not identify the confirmation button. Left the dialog open.')
        button=buttons[0]
        now=self.ui.screen().crop((a,b,c,d))
        # OCR can outlast the dialog.
        for line in lines:
            x,y,w,h=(float(line[k])/2 for k in ('x','y','w','h'))
            box=(int(x),int(y),int(x+w+1),int(y+h+1))
            if np.abs(gray(region.crop(box))-gray(now.crop(box))).mean()>8:
                raise Stop('The dialog changed while it was being read.')
        self.ui.click((round(a+(button['x']+button['w']/2)/2),
                       round(b+(button['y']+button['h']/2)/2)),client_coords=True)
        if funds:
            return 'funds'
        return 'sold' if sold else ('confirmed' if confirm else 'success')

    def buy(self, y):
        frame,page = self.listing()
        if self.find(frame)!=y:
            return False
        self.ui.click((1018,y))
        self.ui.wait(.25)
        frame = self.ui.screen()
        if self.shop(frame) and self.find(frame)!=y:
            return False
        if not self.shop(frame) or not self.matches_name(self.field(frame,NAMEPLATE),'white'):
            raise Stop('The selected item name could not be verified. Check the left-hand nameplate.')
        stock = self.reader.digits(self.field(frame,(814,y-13,872,y+14)))
        if not stock or int(stock)<1:
            raise Stop('The available quantity was unreadable.')
        stock = int(stock)
        print(f'Found {self.name} on page {page[0]} with {stock} available.',flush=True)
        if self.scan_only:
            raise Stop('Scan complete. The matching listing is selected.')
        wallet = self.reader.amount(self.field(frame,WALLET),'your gold balance')
        try:
            price = self.reader.amount(self.field(frame,(1266,y-13,1357,y+14)),'the listed unit price')
        except Stop:
            # An unaffordable row may change text color. The selected item also shows its cost.
            price = self.reader.amount(self.field(frame,(679,619,765,642)),'the selected unit price','black')
        if price < 1:
            raise Stop('The listed unit price must be positive.')
        if wallet < price:
            raise Stop(f'{self.name} costs {price} gold each. You have {wallet} gold.')
        listing = (stock,wallet)
        self.unchanged = self.unchanged+1 if self.last_listing==listing else 0
        if self.unchanged>=2:
            raise Stop('Stock stayed the same after two submissions. Check your gold and inventory.')
        self.last_listing=listing
        use_bulk = stock>1 and not self.single_next and self.reader.matches(self.field(frame,MORE_BUTTON),'Buy More...')
        if use_bulk:
            self.ui.click((680,722))
            end=time.monotonic()+3
            while not self.preview(self.ui.screen(),require_buy=False):
                if time.monotonic()>end:
                    current=self.ui.screen()
                    if not self.shop(current) and not self.reader.matches(self.field(current,DIALOG_TITLE),'Buy More','black'):
                        outcome = self.confirm_notice(current,allow_confirm=False)
                        if outcome in ('sold','funds'):
                            self.last_listing=None
                            self.single_next = outcome=='funds'
                            self.ui.wait(.7)
                            return False
                    raise Stop('Could not verify Buy More and the target name. Check the open dialog.')
                self.ui.wait(.12)
            preview_frame = self.ui.screen()
            if not self.preview(preview_frame,require_buy=False):
                raise Stop('The purchase preview changed before its price could be read.')
            limit = 0
            if self.preview(preview_frame):
                maximum = self.reader.amount(self.field(preview_frame,DIALOG_MAX_UNIT),'the maximum unit cost')
                if maximum < price:
                    raise Stop('The quoted maximum is below the listed price. Check the dialog.')
                limit = min(stock,wallet//maximum)
            if not limit:
                self.ui.click(CANCEL)
                self.listing()
                self.single_next = True
                self.last_listing = None
                return False
            if limit==stock:
                self.ui.click(MAX_SLIDER)
            for _ in range(stock+2):
                frame=self.ui.screen()
                if not self.preview(frame):
                    raise Stop('The Buy More dialog changed.')
                qty=self.quantity(frame)
                if not qty or not 1<=int(qty)<=limit:
                    raise Stop('The selected quantity could not be verified.')
                if int(qty)==limit:
                    break
                self.ui.click(PLUS)
                self.ui.wait(.18)
                after=self.ui.screen()
                if not self.preview(after):
                    raise Stop('The purchase preview changed.')
                if self.quantity(after)==qty:
                    break  # The game may cap a single purchase below the listed stock.
            frame=self.ui.screen()
            if not self.preview(frame) or self.quantity(frame)!=qty:
                raise Stop('Item or quantity changed before purchase.')
            self.ui.click(SUBMIT)
        elif self.reader.matches(self.field(frame,BUY_BUTTON),'Buy'):
            latest=self.ui.screen()
            if (not self.shop(latest)
                    or not np.array_equal(ink(self.field(frame,NAMEPLATE),'white'),ink(self.field(latest,NAMEPLATE),'white'))
                    or not self.reader.matches(self.field(latest,BUY_BUTTON),'Buy')):
                raise Stop('The selected listing changed before purchase.')
            self.ui.click((681,760))
            qty='1'
        else:
            raise Stop('The bulk purchase button is unavailable.')
        print(f'Submitted a request for {qty}. Checking the Bazaar.',flush=True)
        self.ui.wait(.35)
        confirmed = False
        deadline = time.monotonic()+4
        while True:
            frame=self.ui.screen()
            bounds = self.notice_bounds(frame)
            expired = time.monotonic()>=deadline
            if bounds is None and self.page(frame) is not None:
                balance = self.reader.amount(self.field(frame,WALLET),'your remaining gold')
                if balance < wallet or expired:
                    break
                self.ui.wait(.2)
                continue
            if bounds is None and self.preview(frame):
                if expired:
                    raise Stop('The purchase preview stayed open. Check the transaction.')
                self.ui.wait(.2)
                continue
            outcome=self.confirm_notice(frame,allow_confirm=not confirmed,bounds=bounds)
            if outcome=='confirmed':
                confirmed = True
                print('Clicked Yes on the Bazaar purchase confirmation.',flush=True)
                deadline = time.monotonic()+4
            elif outcome=='waiting' and expired:
                raise Stop('The Bazaar confirmation stayed open after clicking Yes.')
            self.ui.wait(.4)
            if outcome in ('funds','sold'):
                self.listing()
                self.last_listing=None
                if outcome=='funds' and not use_bulk:
                    raise Stop(f'The Bazaar says you cannot afford one {self.name}.')
                self.single_next = outcome=='funds'
                return False
            if outcome=='success':
                break
        frame,_ = self.listing()
        self.single_next = False
        if self.reader.amount(self.field(frame,WALLET),'your remaining gold')==0:
            raise Stop('Your gold balance is zero.')
        return True

    def run(self):
        empty_reported = False
        while True:
            frame,_ = self.listing()
            if not self.scan_only and self.reader.amount(self.field(frame,WALLET),'your gold balance')==0:
                raise Stop('Your gold balance is zero.')
            self.ui.click(self.tab)
            self.ui.wait(1)
            frame,page=self.listing()
            while page[0]>1:
                frame,page=self.turn(PREVIOUS,page[0]-1)
            while True:
                if page==(0,0):
                    if self.scan_only:
                        print('Scan complete. The reagent tab is empty.')
                        return
                    if not empty_reported:
                        print('Reagent tab is empty. Refreshing until stock appears.',flush=True)
                    empty_reported = True
                    self.ui.wait(1.2)
                    break
                empty_reported = False
                y=self.find(frame)
                if y is not None:
                    self.buy(y)
                    break
                if page[0]>=page[1]:
                    if self.scan_only:
                        print(f'Scan complete. {self.name} was absent from all {page[1]} pages.')
                        return
                    self.ui.wait(1.2)
                    break
                frame,page=self.turn(NEXT,page[0]+1)


def main():
    print('Wizard101 Bazaar Buyer    |    F10 stops the bot at any time.\n')
    name=input('Reagent name [Outsider Relic]: ').strip() or 'Outsider Relic'
    if len(name)>64 or not all(c.isprintable() for c in name):
        raise Stop('Use the displayed reagent name, up to 64 characters.')
    mode=input('Press Enter to keep buying until gold runs short, or type SCAN for one pass: ').strip().lower()
    if mode not in ('','scan'):
        raise Stop('Choose Enter or SCAN.')
    print('Open Bazaar > Buy > Reagents, select the category to watch,\n'
          'then hover the center of that category icon and press F8. Keep the game in front.',flush=True)
    ui32=ct.windll.user32
    while not ui32.GetAsyncKeyState(0x77)&0x8000:
        if ui32.GetAsyncKeyState(0x79)&0x8000:
            raise Stop('Cancelled.')
        time.sleep(.03)
    ui=Windows()
    pt=wintypes.POINT()
    ui.ui.GetCursorPos(ct.byref(pt))
    cursor=pt.x-ui.bounds[0],pt.y-ui.bounds[1]
    # A hover tooltip can obscure the calibration landmarks.
    clear=(ui.bounds[0]+40,ui.bounds[1]+110)
    ui.ui.SetCursorPos(*clear)
    ui.wait(.45)
    reader=Reader(font_bytes(ui.game_dir))
    ui.layout=calibrate(ui.screen(),cursor,reader)
    tab = min(REAGENT_TABS,key=lambda p: abs(ui.layout.point(*p)[0]-cursor[0]))
    print(f'Watching the chosen reagent tab for {name}.',flush=True)
    Bazaar(ui,reader,name,mode=='scan',tab).run()


if __name__=='__main__':
    try:
        main()
    except (Stop, OSError, KeyboardInterrupt) as e:
        print(f'Stopped. {e}')
