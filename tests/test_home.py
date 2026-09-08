import unittest
from html.parser import HTMLParser
from pathlib import Path

from flask import Flask, render_template


class HomeTests(unittest.TestCase):
    def test_champions_navigation_and_accessible_disclosures(self):
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        with app.test_request_context('/'):
            html = render_template('home.html')
        class Markup(HTMLParser):
            def __init__(self):
                super().__init__(); self.links=[]; self.details=[]; self.years=[]; self.in_link=False
            def handle_starttag(self, tag, attrs):
                attrs=dict(attrs)
                if tag == 'a':
                    assert not self.in_link, 'Nested navigation links'
                    self.in_link=True; self.links.append(attrs.get('href'))
                if tag == 'details':
                    assert not self.in_link, 'Disclosure inside navigation link'
                    self.details.append(attrs)
                if tag == 'time': self.years.append(attrs['datetime'])
            def handle_endtag(self, tag):
                if tag == 'a': self.in_link=False
        parsed=Markup(); parsed.feed(html)
        for league in ('elite','the100','cities','libyan','arab'):
            self.assertEqual(parsed.links.count('/league/'+league),4)
        self.assertLess(html.index('class="quick-leagues"'), html.index('class="intro"'))
        for card in html.split('<article class="league-card"')[1:]:
            self.assertLess(card.index('class="card-action"'),card.index('class="champion"'))
        self.assertEqual(len(parsed.details),5)
        self.assertTrue(all('open' not in attrs for attrs in parsed.details))
        self.assertEqual(len(parsed.years),17)
        self.assertTrue(all(year.isascii() and year.isdigit() for year in parsed.years))
        for winner in ('راشد أبوخريص','عمار دهان','عزيز المحجوب','طرميسة','المستقبل','العين الإماراتي'):
            self.assertIn(winner,html)


if __name__ == '__main__':
    unittest.main()
