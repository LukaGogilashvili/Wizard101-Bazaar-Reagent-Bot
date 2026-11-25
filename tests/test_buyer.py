"""Screen recognition and simulated Bazaar transactions."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
import numpy as np

import bazaar_buyer as bot


def screen_fixture():
    frame=Image.new('RGB',(1920,1080))
    with Image.open(Path(__file__).with_name('listings.png')) as image:
        frame.paste(image,(550,220))
    return frame


class Game:
    """Two-page Bazaar with configurable stock, prices, and restocks."""
    layout=bot.Layout(1,0,0)

    def __init__(self, reader):
        self.reader=reader
        self.page=2
        self.tab=bot.TAB
        self.refreshes=0
        self.stock=6
        self.wallet=855
        self.price=100
        self.maximum=100
        self.restock={}
        self.qty=0
        self.in_dialog=False
        self.selected=False
        self.wrong=False
        self.purchases=[]
        self.visited=[]
        self.base=screen_fixture()

    def label(self, frame, box, text, color, background, height):
        frame.paste(background,box)
        ref=self.reader.render(text)
        size=(round(ref.width*height/ref.height),height)
        x=(box[0]+box[2]-size[0])//2
        y=(box[1]+box[3]-size[1])//2
        frame.paste(color,(x,y,x+size[0],y+size[1]),ref.resize(size,bot.RESAMPLE))

    def screen(self):
        if self.in_dialog:
            frame=Image.new('RGB',(1920,1080),'#b89865')
            self.label(frame,(889,287,1031,320),'Buy More','black','#decaa6',20)
            self.label(frame,bot.DIALOG_NAME,'Ore' if self.wrong else 'Outsider Relic','yellow','maroon',23)
            self.label(frame,bot.DIALOG_COUNT,str(self.qty),'yellow','black',17)
            self.label(frame,bot.DIALOG_BUY,'BUY','yellow','maroon',18)
            self.label(frame,bot.DIALOG_MAX_UNIT,str(self.maximum),'yellow','black',18)
            return frame
        frame=self.base.copy()
        self.label(frame,bot.WALLET,str(self.wallet),'yellow','saddlebrown',18)
        self.label(frame,(1266,383,1357,410),str(self.price),'yellow','maroon',14)
        self.label(frame,bot.PAGE,f'{self.page}/2','black','#e0c798',20)
        if self.page==2 and self.refreshes>=2 and self.stock:
            ref=self.reader.relic
            frame.paste('maroon',(877,383,1162,410))
            frame.paste('yellow',(963,390,963+ref.width,390+ref.height),ref)
            self.label(frame,(814,383,872,410),str(self.stock),'yellow','maroon',14)
            if self.selected:
                self.label(frame,bot.NAMEPLATE,'Outsider Relic','white','navy',18)
        return frame

    def wait(self, seconds):
        pass

    def click(self, point, **kwargs):
        if point==self.tab:
            self.refreshes+=1
            if self.refreshes in self.restock:
                self.stock=self.restock[self.refreshes]
            if self.refreshes>15:
                raise AssertionError('unbounded scan')
        elif point==bot.PREVIOUS:
            self.page-=1
        elif point==bot.NEXT:
            self.page+=1
            self.visited.append((self.refreshes,self.page))
        elif point==(1018,396):
            self.selected=True
        elif point==(680,722):
            assert self.selected and self.stock
            self.in_dialog=True
            self.qty=1
        elif point==bot.MAX_SLIDER:
            self.qty=min(4,self.stock)
        elif point==bot.PLUS:
            self.qty=min(4,self.stock,self.qty+1)
        elif point==bot.CANCEL:
            self.in_dialog=False
        elif point in (bot.SUBMIT,(681,760)):
            assert not self.wrong and self.in_dialog==(point==bot.SUBMIT)
            if not self.in_dialog:
                self.qty=1
            assert self.wallet>=self.price*self.qty
            self.wallet-=self.price*self.qty
            self.purchases.append(self.qty)
            self.stock-=self.qty
            self.in_dialog=False
            self.selected=False
        else:
            raise AssertionError(f'Unexpected click {point}')


class BuyerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(os.getenv('WIZARD101_DIR','C:/ProgramData/KingsIsle Entertainment/Wizard101'))
        cls.reader=bot.Reader(bot.font_bytes(path))

    def test_captured_page(self):
        image=screen_fixture()
        for scale in (.8,1,1.25):
            with self.subTest(scale=scale):
                frame=image.resize((round(image.width*scale),round(image.height*scale)),bot.RESAMPLE)
                layout=bot.calibrate(frame,(round(881*scale),round(312*scale)),self.reader)
                game=Game(self.reader)
                game.layout=layout
                for name,y in [('Cobalt',396),('Lead',434),('Scales',472),('Tin',509),('Outsider Relic',None)]:
                    buyer=bot.Bazaar(game,self.reader,name,True)
                    self.assertEqual(buyer.page(frame),(1,1))
                    self.assertEqual(buyer.find(frame),y,name)
        self.assertEqual(self.reader.digits(image.crop((814,383,872,410))),'134')
        self.assertEqual(self.reader.amount(image.crop(bot.WALLET),'balance'),507192)
        self.assertEqual(self.reader.amount(image.crop((1266,383,1357,410)),'unit price'),550)

    def test_scan_and_purchase_batches(self):
        game=Game(self.reader)
        game.restock={5:10}
        game.tab=bot.REAGENT_TABS[1]
        buyer=bot.Bazaar(game,self.reader,'Outsider Relic',False,game.tab)
        with self.assertRaisesRegex(bot.Stop,'You have 55 gold'):
            buyer.run()
        self.assertEqual(game.purchases,[4,2,2])
        self.assertIn((1,2),game.visited)
        self.assertIn((4,2),game.visited)
        self.assertEqual(game.stock,8)

    def test_blank_tab_refreshes_until_page_two_restocks(self):
        game=Game(self.reader)
        game.wallet=100
        empty=screen_fixture()
        with Image.open(Path(__file__).with_name('empty-reagents.png')) as captured:
            empty.paste(captured,(548,234))
        buyer=bot.Bazaar(game,self.reader,'Outsider Relic',False)
        for scale in (.8,1,1.25):
            frame=empty.resize((round(1920*scale),round(1080*scale)),bot.RESAMPLE)
            with self.subTest(scale=scale), patch.object(buyer,'layout',bot.Layout(scale,0,0)):
                self.assertEqual(buyer.page(frame),(0,0))

        populated=game.screen
        def screen():
            return empty if game.refreshes<4 else populated()
        with patch.object(game,'screen',screen), self.assertRaisesRegex(bot.Stop,'balance is zero'):
            buyer.run()
        self.assertEqual(game.refreshes,4)
        self.assertEqual(game.visited,[(4,2)])
        self.assertEqual(game.purchases,[1])

        covered=populated()
        covered.paste(empty.crop(bot.PAGE),bot.PAGE)
        self.assertIsNone(buyer.page(covered))

    def test_single_at_listed_price_when_bulk_maximum_exceeds_gold(self):
        game=Game(self.reader)
        game.wallet=205
        game.maximum=1000
        with self.assertRaisesRegex(bot.Stop,'You have 5 gold'):
            bot.Bazaar(game,self.reader,'Outsider Relic',False).run()
        self.assertEqual(game.purchases,[1,1])

    def test_single_iron_from_captured_listing(self):
        game=Game(self.reader)
        with Image.open(Path(__file__).with_name('iron-listing.png')) as capture:
            frame=capture.convert('RGB')
        game.layout=bot.calibrate(frame,(350,92),self.reader)
        buyer=bot.Bazaar(game,self.reader,'iron',False)
        y=buyer.find(frame)
        self.assertEqual(y,585)
        with patch.object(game,'screen',return_value=frame), patch.object(game,'click') as click:
            self.assertTrue(buyer.buy(y))
            self.assertEqual([c.args[0] for c in click.call_args_list],[(1018,y),(681,760)])

            frame.paste(screen_fixture().crop(bot.NAMEPLATE),game.layout.point(*bot.NAMEPLATE[:2]))
            click.reset_mock()
            with self.assertRaisesRegex(bot.Stop,'selected item name'):
                buyer.buy(y)
            click.assert_called_once_with((1018,y))

    def test_windows_ocr_returns_word_coordinates(self):
        lines=bot.read_ink(np.asarray(self.reader.render('Cancel'))>127)
        self.assertTrue(lines)
        word=lines[0]['words'][0]
        self.assertTrue(word['text'])
        self.assertGreater(word['w'],0)

    def test_captured_brass_purchase(self):
        game=Game(self.reader)
        game.stock=49
        game.wallet=50000
        game.page=1
        game.label(game.base,(877,383,1162,410),'Brass','yellow','maroon',14)
        game.label(game.base,(814,383,872,410),'49','yellow','maroon',14)
        game.label(game.base,bot.NAMEPLATE,'Brass','white','navy',17)
        original_screen=game.screen
        captured=Image.new('RGB',(1920,1080))
        with Image.open(Path(__file__).with_name('buy-more-brass.png')) as im:
            captured.paste(im,(724,221))

        def screen():
            if not game.in_dialog:
                return original_screen()
            frame=captured.copy()
            if game.qty>1:
                game.label(frame,bot.DIALOG_COUNT,str(game.qty),'#bebe00','black',18)
            return frame

        original_click=game.click
        def click(pos):
            if pos==bot.MAX_SLIDER:
                game.qty=49
            else:
                original_click(pos)

        buyer=bot.Bazaar(game,self.reader,'Brass',False)
        self.assertEqual(self.reader.amount(captured.crop(bot.DIALOG_MAX_UNIT),'maximum cost'),1000)
        for scale,drift in ((.8,-2),(1,0),(1.25,3)):
            frame=captured.resize((round(1920*scale),round(1080*scale)),bot.RESAMPLE)
            with self.subTest(scale=scale), patch.object(buyer,'layout',bot.Layout(scale,drift,drift)):
                self.assertTrue(buyer.preview(frame))
                self.assertEqual(buyer.quantity(frame),'1')
        with patch.object(game,'screen',screen), patch.object(game,'click',click):
            self.assertTrue(buyer.buy(396))
        self.assertEqual(game.purchases,[49])

    def test_scan_only_stops_with_stock(self):
        game=Game(self.reader)
        game.refreshes=1
        with self.assertRaisesRegex(bot.Stop,'Scan complete'):
            bot.Bazaar(game,self.reader,'Outsider Relic',True).run()
        self.assertEqual(game.purchases,[])

    def test_wrong_dialog_stops_before_buy(self):
        game=Game(self.reader)
        game.wrong=True
        with patch('bazaar_buyer.time.monotonic',side_effect=range(10000)):
            with self.assertRaisesRegex(bot.Stop,'Could not verify Buy More'):
                bot.Bazaar(game,self.reader,'Outsider Relic',False).run()
        self.assertEqual(game.purchases,[])

    def test_suffixed_name_is_different(self):
        ref=self.reader.render('Outsider Relics')
        frame=Image.new('RGB',ref.size,'maroon')
        frame.paste('yellow',(0,0,*ref.size),ref)
        self.assertFalse(self.reader.matches(frame,'Outsider Relic',item=True))
        self.assertNotEqual(bot.normalized('Outsider Relics'),bot.normalized('Outsider Relic'))

    def test_confirmation_checks_full_item_name(self):
        game=Game(self.reader)
        buyer=bot.Bazaar(game,self.reader,'Outsider Relic',False)
        def word(text,x,y):
            return dict(text=text,x=x,y=y,w=60,h=24)
        buttons=word('Yes No',380,390)
        buttons['words']=[word('Yes',380,390),word('No',620,390)]
        for name,correct in [('Outsider Relics',False),('Outsider Relic',True)]:
            lines=[word(f'Are you sure you want to buy the {name} for 350 gold?',50,190),buttons]
            with patch('bazaar_buyer.read_words',return_value=lines), patch.object(game,'click') as clicked:
                if correct:
                    self.assertEqual(buyer.confirm_notice(game.screen()),'confirmed')
                    clicked.assert_called_once()
                else:
                    with self.assertRaisesRegex(bot.Stop,'Unrecognized'):
                        buyer.confirm_notice(game.screen())
                    clicked.assert_not_called()

    def test_gold_confirmation_over_visible_shop(self):
        for name,stock in [('Iron',1),('Outsider Relic',3)]:
            with self.subTest(name=name):
                game=Game(self.reader)
                game.refreshes=2
                game.stock=stock
                buyer=bot.Bazaar(game,self.reader,name,False)
                with Image.open(Path(__file__).with_name('bazaar-confirm-iron.png')) as im:
                    popup=im.convert('RGB')
                original_screen,original_click=game.screen,game.click
                if name=='Iron':
                    frame=screen_fixture()
                    with Image.open(Path(__file__).with_name('iron-listing.png')) as im:
                        frame.paste(im,(527,216))
                    def shop_screen():
                        return frame.copy()
                    y=585
                else:
                    draw=ImageDraw.Draw(popup)
                    draw.rectangle((16,65,449,135),fill='#c8a573')
                    font=ImageFont.truetype('arialbd.ttf',18)
                    draw.text((230,84),'Are you sure you want to buy the',font=font,fill='black',anchor='mm')
                    draw.text((230,111),'Outsider Relic for 125,000 gold?',font=font,fill='black',anchor='mm')
                    shop_screen=original_screen
                    y=396
                # The server shows an unchanged shop before drawing its modal.
                pending=False
                checks=0
                paid=False
                def screen():
                    nonlocal checks
                    f=shop_screen()
                    if pending:
                        checks+=1
                        if checks>=3:
                            f.paste(popup,(725,438))
                            self.assertIsNotNone(buyer.page(f))
                    if paid:
                        game.label(f,bot.WALLET,'50','yellow','saddlebrown',18)
                    return f
                def click(pos,client_coords=False):
                    nonlocal paid,pending
                    if client_coords:
                        self.assertTrue(pending)
                        self.assertTrue(896<=pos[0]<=1030 and 610<=pos[1]<=640,pos)
                        paid,pending=True,False
                    elif pos in ((681,760),bot.SUBMIT):
                        self.assertEqual(pos,(681,760) if stock==1 else bot.SUBMIT)
                        game.in_dialog=False
                        pending=True
                    elif stock>1:
                        original_click(pos)
                    else:
                        self.assertEqual(pos,(1018,y))
                with patch.object(game,'screen',screen),patch.object(game,'click',click):
                    self.assertTrue(buyer.buy(y))
                self.assertTrue(paid)


if __name__=='__main__':
    unittest.main()
