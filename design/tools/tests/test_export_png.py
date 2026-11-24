import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import export_png as ep  # noqa: E402


class NameTest(unittest.TestCase):
    def test_round_suffix(self):
        self.assertEqual(ep.round_suffix(1), '')
        self.assertEqual(ep.round_suffix(2), '_01')
        self.assertEqual(ep.round_suffix(11), '_10')
        with self.assertRaises(ValueError):
            ep.round_suffix(0)

    def test_safe_tab_name(self):
        self.assertEqual(ep.safe('6-1-1. 시퀀스: 주문'), '6-1-1. 시퀀스_ 주문')
        self.assertEqual(ep.safe('a/b\\c*d?e"f<g>h|i'), 'a_b_c_d_e_f_g_h_i')

    def test_file_name_first_round_single_tab(self):
        self.assertEqual(ep.file_name('StateDiagram', 1, None), '2팀_StateDiagram.png')

    def test_file_name_second_round_with_tab(self):
        # 지난 Scenario_2nd 제출과 같은 모양
        self.assertEqual(ep.file_name('SequenceDiagram', 2, '6-1-1. 시퀀스: 주문'),
                         '2팀_SequenceDiagram_01-6-1-1. 시퀀스_ 주문.png')

    def test_file_name_with_label(self):
        self.assertEqual(ep.file_name('GUIDesign', 1, '0. 화면 구성', label='관리자'),
                         '2팀_GUIDesign-관리자-0. 화면 구성.png')


class AskTest(unittest.TestCase):
    def answers(self, *xs):
        it = iter(xs)
        return lambda prompt: next(it)

    def test_ask_round_retries_until_valid(self):
        self.assertEqual(ep.ask_round(self.answers('', 'abc', '0', ' 3 ')), 3)

    def test_ask_out_dir_default(self):
        self.assertEqual(ep.ask_out_dir(3, self.answers('')), 'design/exports/제출_3차')
        self.assertEqual(ep.ask_out_dir(3, self.answers(' design/exports/x ')), 'design/exports/x')

    def test_confirm(self):
        self.assertTrue(ep.confirm(self.answers('')))
        self.assertTrue(ep.confirm(self.answers('y')))
        self.assertFalse(ep.confirm(self.answers('n')))


class PlanTest(unittest.TestCase):
    TABS = {'ifp.drawio': ['0. 상태표', '1. 순서도', '2. 주문', '6-1-1. 시퀀스: 주문', '6-2. 시퀀스: 결품'],
            'admin.drawio': ['0. 화면 구성', '1. 평상시 현황'],
            'one.drawio': ['구성']}
    RULES = [
        {'file': 'ifp.drawio', 'kind': 'StateDiagram', 'tabs': ['0.', '2.']},
        {'file': 'ifp.drawio', 'kind': 'SequenceDiagram', 'tabs': ['6-']},
        {'file': 'admin.drawio', 'kind': 'GUIDesign', 'label': '관리자'},
        {'file': 'one.drawio', 'kind': 'ClassDiagram'},
        {'file': 'off.drawio', 'kind': 'ERDiagram', 'enabled': False},
    ]

    def plan(self, **kw):
        return ep.plan(self.RULES, 2, lambda f: self.TABS[f], **kw)

    def test_tab_filters_and_numbering(self):
        got = [(j['file'], j['page'], j['out']) for j in self.plan()]
        self.assertEqual(got, [
            ('ifp.drawio', 1, '2팀_StateDiagram_01-0. 상태표.png'),
            ('ifp.drawio', 3, '2팀_StateDiagram_01-2. 주문.png'),
            ('ifp.drawio', 4, '2팀_SequenceDiagram_01-6-1-1. 시퀀스_ 주문.png'),
            ('ifp.drawio', 5, '2팀_SequenceDiagram_01-6-2. 시퀀스_ 결품.png'),
            ('admin.drawio', 1, '2팀_GUIDesign_01-관리자-0. 화면 구성.png'),
            ('admin.drawio', 2, '2팀_GUIDesign_01-관리자-1. 평상시 현황.png'),
            ('one.drawio', 1, '2팀_ClassDiagram_01.png'),   # 탭 하나뿐인 종류는 탭 이름을 붙이지 않음
        ])

    def test_only_kinds(self):
        got = {j['kind'] for j in self.plan(only=['GUIDesign'])}
        self.assertEqual(got, {'GUIDesign'})

    def test_names_are_unique(self):
        outs = [j['out'] for j in self.plan()]
        self.assertEqual(len(outs), len(set(outs)))


if __name__ == '__main__':
    unittest.main()
