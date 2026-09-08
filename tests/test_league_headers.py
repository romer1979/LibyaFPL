import unittest
from pathlib import Path
from xml.etree import ElementTree

from flask import Flask, render_template_string


class LeagueHeaderTests(unittest.TestCase):
    def test_dashboard_headers_keep_navigation_and_brand_scoping(self):
        root = Path(__file__).resolve().parents[1]
        app = Flask(__name__, static_folder=str(root / 'static'))
        for league in ('elite', 'cities', 'libyan', 'arab'):
            filename = 'dashboard.html' if league == 'elite' else league + '_dashboard.html'
            source = (root / 'templates' / filename).read_text().split('</header>')[0] + '</header>'
            with app.test_request_context('/'):
                html = render_template_string(source, data={'gameweek':4}, ar={})
            self.assertIn(f'class="league-home brand-{league}"', html)
            self.assertIn('css/league_headers.css', html)
            self.assertIn(f'/league/{league}/planner', html)
            self.assertIn(f'/league/{league}/history', html)
            if league != 'elite':
                self.assertLess(html.index(f'css/{league}.css'), html.index('css/league_headers.css'))
        for name in ('cities', 'libyan', 'arab'):
            asset = 'header_stadium.svg' if name == 'libyan' else f'header_{name}.svg'
            self.assertEqual(ElementTree.parse(root / 'static' / asset).getroot().tag,
                             '{http://www.w3.org/2000/svg}svg')
            self.assertEqual(app.test_client().get('/static/' + asset).status_code, 200)


if __name__ == '__main__':
    unittest.main()
