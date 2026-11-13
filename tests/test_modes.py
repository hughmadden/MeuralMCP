"""Mode clients preserve explicit selection, native errors and discovery metadata."""
import asyncio,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch,call
from fastapi.testclient import TestClient
from meural_mcp.api import create_app
from meural_mcp.local import FrameLocalClient,FrameLocalError
from meural_mcp.manager import ManagerService
from meural_mcp.api_client import RemoteApiClient
from meural_mcp.mcp_server import (build_mcp_server,mcp_get_device_modes,mcp_select_device_mode,
                                  mcp_navigate_device_menu,mcp_register_device_mode,
                                  mcp_get_device_mode_events,mcp_set_device_menu_timeout,mcp_unregister_device_mode)
from test_features import png_bytes

MODE={'id':'reef','label':'Reef','menu':[{'id':'feed','label':'Feed','action':'feed'}]}
AUTH={'Authorization':'Bearer test-token'}

class ModeClientTests(unittest.TestCase):
 def test_native_registration_sends_nested_json_and_unchanged_frame_token(self):
  response=Mock(status_code=200,json=Mock(return_value={'is_ok':True}))
  with patch('meural_mcp.local.requests.post',return_value=response) as post:
   FrameLocalClient('192.0.2.10',token='test-frame-token').register_mode(MODE)
  post.assert_called_once_with('http://192.0.2.10:8099/local/modes',json=MODE,timeout=5,params={'token':'test-frame-token'})
 def test_native_select_navigation_cursor_and_timeout_have_distinct_calls(self):
  client=FrameLocalClient('192.0.2.10');client.post=Mock();client.get=Mock();client._delete=Mock()
  client.select_mode('reef');client.navigate_menu('left');client.mode_events(4,'epoch')
  client.set_menu_timeout('gallery',None,'pictures');client.unregister_mode('reef')
  self.assertEqual(client.post.call_args_list,[call('/local/modes/reef/select'),call('/local/action',data={'do':'navigate','value':'left'}),call('/local/modes/gallery/timeout',data={'seconds':'inherit','menu_id':'pictures'})])
  client.get.assert_called_once_with('/local/modes/events',params={'after':4,'epoch':'epoch'})
  client._delete.assert_called_once_with('/local/modes/reef')
 def test_remote_routes_preserve_explicit_selection_and_null_reset(self):
  client=RemoteApiClient('https://example.invalid','test-token');client.request=Mock()
  client.modes('canvas-1');client.navigate_menu('canvas-1','right');client.select_mode('canvas-1','reef')
  client.register_mode('canvas-1',MODE);client.mode_events('canvas-1',7,'epoch')
  client.set_menu_timeout('canvas-1','gallery',None);client.unregister_mode('canvas-1','reef')
  self.assertEqual(client.request.call_args_list,[call('GET','/devices/canvas-1/modes'),call('POST','/devices/canvas-1/menu',json={'direction':'right'}),call('POST','/devices/canvas-1/modes/reef/select'),call('POST','/devices/canvas-1/modes',json=MODE),call('GET','/devices/canvas-1/modes/events',params={'after':7,'epoch':'epoch'}),call('POST','/devices/canvas-1/modes/gallery/timeout',json={'seconds':None,'menu_id':None}),call('DELETE','/devices/canvas-1/modes/reef')])

class ModeManagerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.client=Mock()
  self.manager=ManagerService(root=Path(self.tmp.name),config={'devices':[{'name':'canvas-1','local_ip':'192.0.2.10','api_token':'test-frame-token'}]},local_client_factory=lambda host,timeout=5:self.client)
 def test_validation_rejects_invalid_identifiers_controls_and_bounds_before_transport(self):
  for operation,args in ((self.manager.select_mode,('../escape',)),(self.manager.unregister_mode,('bad/name',)),(self.manager.navigate_menu,('diagonal',)),(self.manager.set_menu_timeout,('gallery',False)),(self.manager.set_menu_timeout,('gallery',3601)),(self.manager.mode_events,(-1,)),(self.manager.register_mode,({'id':'reef','label':'x'*65536,'menu':[]},))):
   with self.assertRaises(ValueError):operation('canvas-1',*args)
  self.assertEqual(self.client.mock_calls,[])
 def test_mode_failures_keep_native_conflict_code_and_network_errors_private(self):
  self.client.select_mode.side_effect=FrameLocalError(409,'http://192.0.2.10/modes','{"is_ok":false,"error":"mode disabled"}')
  result=self.manager.select_mode('canvas-1','reef')
  self.assertEqual((result['status'],result['http_status'],result['error']),('failed',409,'mode disabled'))
 def test_status_exposes_authoritative_mode_list_and_pending_candidate(self):
  controls={'active_mode':'gallery','pending_mode':'reef','menu_kind':'picker','modes':[{'id':'gallery','label':'Gallery','enabled':True}]}
  self.client.identify.return_value={'orientation':'landscape','controls':controls};self.client.current_gallery.return_value={}
  result=self.manager.device_status('canvas-1')
  self.assertEqual(result['current_mode'],'gallery');self.assertEqual(result['modes'],controls['modes']);self.assertEqual(result['controls']['pending_mode'],'reef')
 def test_renderer_registration_survives_rest_status_and_preserves_older_unknown(self):
  definition=dict(MODE,menu_renderer='external')
  catalog=[{'id':'gallery','label':'Gallery','enabled':True,'menu_renderer':'x11'},
           {'id':'legacy','label':'Legacy','enabled':True}]
  def register(value):
   catalog.append({key:value[key] for key in ('id','label','menu_renderer')} | {'enabled':True})
   return {'is_ok':True,'modes':catalog}
  self.client.register_mode.side_effect=register
  self.client.identify.side_effect=lambda:{'orientation':'landscape','controls':{'active_mode':'reef','modes':catalog}}
  self.client.current_gallery.return_value={}
  with patch.dict(os.environ,{'MEURAL_MCP_API_TOKEN':'test-token'}),patch('meural_mcp.api.service',return_value=self.manager):
   client=TestClient(create_app())
   self.assertEqual(client.post('/devices/canvas-1/modes',headers=AUTH,json=definition).status_code,200)
   response=client.get('/devices/canvas-1',headers=AUTH)
  self.assertEqual(response.status_code,200)
  self.client.register_mode.assert_called_once_with(definition)
  modes={value['id']:value for value in response.json()['modes']}
  self.assertEqual(modes['reef']['menu_renderer'],'external')
  self.assertEqual(modes['gallery']['menu_renderer'],'x11')
  self.assertIsNone(modes['legacy']['menu_renderer'])
 def test_unavailable_mode_status_is_unknown(self):
  self.client.identify.side_effect=OSError('unavailable')
  result=self.manager.device_status('canvas-1')
  self.assertIsNone(result['current_mode']);self.assertEqual(result['modes'],[]);self.assertIsNone(result['controls'])
 def test_main_image_upload_selects_native_gallery_item_without_mode_change(self):
  self.client.create_gallery.return_value='7';self.client.upload_item.return_value='8'
  self.client.gallery_items.return_value=[{'id':'old'}]
  result=self.manager.set_image('canvas-1',png_bytes(),filename='picture.png')
  self.assertEqual(result['status'],'pass')
  self.client.change_gallery.assert_called_once_with('7');self.client.change_item.assert_called_once_with('8')
  self.client.delete_item.assert_called_once_with('old')
  self.client.select_mode.assert_not_called();self.client.navigate_menu.assert_not_called()

class ModeSurfaceTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{'MEURAL_MCP_API_TOKEN':'test-token'});self.env.start();self.addCleanup(self.env.stop)
  self.manager=Mock();self.patch=patch('meural_mcp.api.service',return_value=self.manager);self.patch.start();self.addCleanup(self.patch.stop)
  for method in ('modes','register_mode','unregister_mode','select_mode','navigate_menu','mode_events','set_menu_timeout'):getattr(self.manager,method).return_value={'status':'pass','controls':{'active_mode':'gallery'}}
  self.client=TestClient(create_app())
 def test_rest_operations_forward_without_implicit_selection(self):
  self.client.get('/devices/canvas-1/modes',headers=AUTH)
  self.client.post('/devices/canvas-1/menu',headers=AUTH,json={'direction':'right'})
  self.manager.select_mode.assert_not_called()
  self.client.post('/devices/canvas-1/modes/reef/select',headers=AUTH)
  self.manager.select_mode.assert_called_once_with('canvas-1','reef')
  self.client.post('/devices/canvas-1/modes',headers=AUTH,json=MODE)
  self.manager.register_mode.assert_called_once_with('canvas-1',MODE)
  self.client.get('/devices/canvas-1/modes/events?after=4&epoch=test',headers=AUTH)
  self.manager.mode_events.assert_called_once_with('canvas-1',4,'test')
  self.client.post('/devices/canvas-1/modes/gallery/timeout',headers=AUTH,json={'seconds':None})
  self.manager.set_menu_timeout.assert_called_once_with('canvas-1','gallery',None,None)
  self.client.delete('/devices/canvas-1/modes/reef',headers=AUTH)
  self.manager.unregister_mode.assert_called_once_with('canvas-1','reef')
 def test_auth_validation_and_native_http_conflicts(self):
  self.assertEqual(self.client.post('/devices/canvas-1/modes/reef/select').status_code,401)
  self.manager.select_mode.assert_not_called()
  self.assertEqual(self.client.post('/devices/canvas-1/menu',headers=AUTH,json={'direction':'diagonal'}).status_code,422)
  for seconds in (False,0,3601):self.assertEqual(self.client.post('/devices/canvas-1/modes/gallery/timeout',headers=AUTH,json={'seconds':seconds}).status_code,422)
  self.manager.select_mode.return_value={'status':'failed','http_status':409,'error':'disabled'}
  self.assertEqual(self.client.post('/devices/canvas-1/modes/reef/select',headers=AUTH).status_code,409)
 def test_swagger_describes_automatic_publish_and_mode_routes(self):
  spec=self.client.get('/openapi.json').json()
  for method in ('post','put'):
   description=spec['paths']['/devices/{name}/image'][method]['description']
   self.assertIn('select gallery/item',description);self.assertIn('does not select a display mode',description)
  self.assertIn('/devices/{name}/modes/{mode_id}/select',spec['paths'])
  self.assertIn('current_mode',spec['components']['schemas']['DeviceStatus']['properties'])
 def test_mcp_tools_advertise_and_forward_mode_actions(self):
  tools=asyncio.run(build_mcp_server().list_tools());names={tool.name for tool in tools}
  self.assertTrue({'get_device_modes','select_device_mode','navigate_device_menu','register_device_mode','unregister_device_mode','get_device_mode_events','set_device_menu_timeout'}<=names)
  with patch('meural_mcp.mcp_server._remote_client',return_value=None),patch('meural_mcp.mcp_server._service',return_value=self.manager):
   mcp_get_device_modes('canvas-1');mcp_navigate_device_menu('canvas-1','right');mcp_select_device_mode('canvas-1','reef')
   mcp_register_device_mode('canvas-1',MODE);mcp_unregister_device_mode('canvas-1','reef')
   mcp_get_device_mode_events('canvas-1',4,'epoch');mcp_set_device_menu_timeout('canvas-1','gallery',None)
  self.manager.navigate_menu.assert_called_with('canvas-1',direction='right')
  self.manager.select_mode.assert_called_with('canvas-1',identifier='reef')

if __name__=='__main__':unittest.main()
