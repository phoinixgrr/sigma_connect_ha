import sys, types, pathlib, importlib, unittest, ast, json
root=pathlib.Path(__file__).resolve().parents[2]/'custom_components'/'sigma_connect_ha'
pkg=types.ModuleType('partition_protocol_test_pkg');pkg.__path__=[str(root)];sys.modules[pkg.__name__]=pkg
from bs4 import BeautifulSoup
from partition_protocol_test_pkg.partition_client import PartitionClient
from unittest.mock import patch

def soup(part,file):
    state = 'OΠΛIΣMENO ME ZΩNEΣ BYPASS' if part == '1' else 'AΦOΠΛIΣMENO'
    common=f'<div>Τμήμα {part}: {state}</div><div>Μπαταρία: 13.7 Volt</div><div>Παροχή 230V: NAI</div>'
    if 'zones' in file:
        count=17 if part=='1' else 5
        rows=''.join(f'<tr><td>{i}</td><td>Example zone {i}</td><td>Κλειστή</td><td>OXI</td></tr>' for i in range(1,count+1))
        body='<table class="normaltable"><tr><th>Zone</th><th>Name</th><th>Status</th><th>Bypass</th></tr>'+rows+'</table>'
    elif 'Ixion' in file:
        body='<a href="/arm">Arm</a><a href="/disarm">Disarm</a><a href="/stay">Stay</a>'
    else:
        body='<a href="zones.html">Zones</a>'
    return BeautifulSoup(common+body,'html.parser')

class Tests(unittest.TestCase):
    def client(self):
        return PartitionClient('http://alarm.example:5053','test','test','1111',None,send_analytics=False,second_pin='2222')
    def test_fixture_partitions(self):
        c=self.client()
        for part,count,state in [('1',17,'Armed'),('2',5,'Disarmed')]:
            c.select_partition=lambda p: soup(p,'Sigma Panel Control.html')
            c._get_soup=lambda path,p=part: soup(p,'Sigma Panel zones.html')
            data=c.read_partition(part)
            self.assertEqual(len(data['zones']),count);self.assertEqual(data['status'],state)
        self.assertTrue(c.parse_alarm_status(c.parse_zones_html(soup('1','Sigma Panel zones.html'))['alarm_status'])[1])
    def test_wrong_partition_blocks_read(self):
        c=self.client();c.select_partition=lambda p: None;c._get_soup=lambda p:soup('2','Sigma Panel zones.html');c.logout=lambda:None
        with self.assertRaises(ValueError):c.read_partition('1')
    def test_pin_switch_and_reuse(self):
        c=self.client();calls=[]
        def login():calls.append(c.pin)
        c.logout=lambda:None;c.login=login
        c._get_soup=lambda path:soup('1' if c.pin=='1111' else '2','Sigma Panel Control.html')
        for p in ['1','1','2','1']:c.select_partition(p)
        self.assertEqual(calls,['1111','2222','1111'])
    def test_wrong_access_blocks_command(self):
        c=self.client();c.read_partition=lambda p:{'status':'Disarmed'};c.select_partition=lambda p:None
        c._get_soup=lambda path:soup('2','Sigma Ixion - Panel.html')
        with patch('requests.Session') as session:
            with self.assertRaises(ValueError):c.action_partition('arm','1')
            session.assert_not_called()
    def test_action_uses_advertised_link_and_verifies(self):
        c=self.client();states=iter(['Disarmed','Armed']);c.read_partition=lambda p:{'status':next(states)}
        c.select_partition=lambda p:None;c._get_soup=lambda path:soup('1','Sigma Ixion - Panel.html')
        with patch('requests.Session') as session,patch('partition_protocol_test_pkg.partition_client.time.sleep'):
            session.return_value.__enter__.return_value.cookies={}
            c.action_partition('arm','1')
            args,kwargs=session.return_value.__enter__.return_value.get.call_args
            self.assertEqual(args[0],c.base_url+'/arm');self.assertFalse(kwargs['allow_redirects'])
    def test_missing_link_blocks_command(self):
        c=self.client();c.read_partition=lambda p:{'status':'Disarmed'};c.select_partition=lambda p:None
        c._get_soup=lambda path:soup('1','Sigma Panel Control.html')
        with patch('requests.Session') as session:
            with self.assertRaises(ValueError):c.action_partition('arm','1')
            session.assert_not_called()
    def test_source_and_json(self):
        for f in root.rglob('*.py'):ast.parse(f.read_text(encoding='utf-8'))
        for f in root.rglob('*.json'):json.loads(f.read_text(encoding='utf-8'))
unittest.main(verbosity=2)
