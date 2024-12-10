# -*- coding: utf-8 -*-
"""
Copyright (C) 2024 Xiaomi Corporation.

The ownership and intellectual property rights of Xiaomi Home Assistant
Integration and related Xiaomi cloud service API interface provided under this
license, including source code and object code (collectively, "Licensed Work"),
are owned by Xiaomi. Subject to the terms and conditions of this License, Xiaomi
hereby grants you a personal, limited, non-exclusive, non-transferable,
non-sublicensable, and royalty-free license to reproduce, use, modify, and
distribute the Licensed Work only for your use of Home Assistant for
non-commercial purposes. For the avoidance of doubt, Xiaomi does not authorize
you to use the Licensed Work for any other purpose, including but not limited
to use Licensed Work to develop applications (APP), Web services, and other
forms of software.

You may reproduce and distribute copies of the Licensed Work, with or without
modifications, whether in source or object form, provided that you must give
any other recipients of the Licensed Work a copy of this License and retain all
copyright and disclaimers.

Xiaomi provides the Licensed Work on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied, including, without
limitation, any warranties, undertakes, or conditions of TITLE, NO ERROR OR
OMISSION, CONTINUITY, RELIABILITY, NON-INFRINGEMENT, MERCHANTABILITY, or
FITNESS FOR A PARTICULAR PURPOSE. In any event, you are solely responsible
for any direct, indirect, special, incidental, or consequential damages or
losses arising from the use or inability to use the Licensed Work.

Xiaomi reserves all rights not expressly granted to you in this License.
Except for the rights expressly granted by Xiaomi under this License, Xiaomi
does not authorize you in any form to use the trademarks, copyrights, or other
forms of intellectual property rights of Xiaomi and its affiliates, including,
without limitation, without obtaining other written permission from Xiaomi, you
shall not use "Xiaomi", "Mijia" and other words related to Xiaomi or words that
may make the public associate with Xiaomi in any form to publicize or promote
the software or hardware devices that use the Licensed Work.

Xiaomi has the right to immediately terminate all your authorization under this
License in the event:
1. You assert patent invalidation, litigation, or other claims against patents
or other intellectual property rights of Xiaomi or its affiliates; or,
2. You make, have made, manufacture, sell, or offer to sell products that knock
off Xiaomi or its affiliates' products.

MIoT http client.
"""
import asyncio
import base64
import json
import logging
import re
import time
from functools import partial
from typing import Optional
from urllib.parse import urlencode
import requests

# pylint: disable=relative-beyond-top-level
from .common import calc_group_id
from .const import (
    DEFAULT_OAUTH2_API_HOST,
    MIHOME_HTTP_API_TIMEOUT,
    OAUTH2_AUTH_URL)
from .miot_error import MIoTErrorCode, MIoTHttpError, MIoTOauthError

_LOGGER = logging.getLogger(__name__)

TOKEN_EXPIRES_TS_RATIO = 0.7


class MIoTOauthClient:
    """oauth agent url, default: product env."""
    _main_loop: asyncio.AbstractEventLoop = None
    _oauth_host: str = None
    _client_id: int
    _redirect_url: str

    def __init__(
            self, client_id: str, redirect_url: str, cloud_server: str,
            loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> None:
        self._main_loop = loop or asyncio.get_running_loop()
        if client_id is None or client_id.strip() == '':
            raise MIoTOauthError('invalid client_id')
        if not redirect_url:
            raise MIoTOauthError('invalid redirect_url')
        if not cloud_server:
            raise MIoTOauthError('invalid cloud_server')

        self._client_id = int(client_id)
        self._redirect_url = redirect_url
        if cloud_server == 'cn':
            self._oauth_host = DEFAULT_OAUTH2_API_HOST
        else:
            self._oauth_host = f'{cloud_server}.{DEFAULT_OAUTH2_API_HOST}'

    async def __call_async(self, func):
        return await self._main_loop.run_in_executor(executor=None, func=func)

    def set_redirect_url(self, redirect_url: str) -> None:
        if not isinstance(redirect_url, str) or redirect_url.strip() == '':
            raise MIoTOauthError('invalid redirect_url')
        self._redirect_url = redirect_url

    def gen_auth_url(
        self,
        redirect_url: Optional[str] = None,
        state: Optional[str] = None,
        scope: Optional[list] = None,
        skip_confirm: Optional[bool] = False,
    ) -> str:
        """get auth url

        Args:
            redirect_url
            state
            scope (list, optional):
                开放数据接口权限 ID，可以传递多个，用空格分隔，具体值可以参考开放
                [数据接口权限列表](https://dev.mi.com/distribute/doc/details?pId=1518).
                Defaults to None.\n
            skip_confirm (bool, optional):
                默认值为true，授权有效期内的用户在已登录情况下，不显示授权页面，直接通过。
                如果需要用户每次手动授权，设置为false. Defaults to True.\n

        Returns:
            str: _description_
        """
        params: dict = {
            'redirect_uri': redirect_url or self._redirect_url,
            'client_id': self._client_id,
            'response_type': 'code',
        }
        if state:
            params['state'] = state
        if scope:
            params['scope'] = ' '.join(scope).strip()
        params['skip_confirm'] = skip_confirm
        encoded_params = urlencode(params)

        return f'{OAUTH2_AUTH_URL}?{encoded_params}'

    def _get_token(self, data) -> dict:
        http_res = requests.get(
            url=f'https://{self._oauth_host}/app/v2/ha/oauth/get_token',
            params={'data': json.dumps(data)},
            headers={'content-type': 'application/x-www-form-urlencoded'},
            timeout=MIHOME_HTTP_API_TIMEOUT
        )
        if http_res.status_code == 401:
            raise MIoTOauthError(
                'unauthorized(401)', MIoTErrorCode.CODE_OAUTH_UNAUTHORIZED)
        if http_res.status_code != 200:
            raise MIoTOauthError(
                f'invalid http status code, {http_res.status_code}')

        res_obj = http_res.json()
        if (
            not res_obj
            or res_obj.get('code', None) != 0
            or 'result' not in res_obj
            or not all(
                key in res_obj['result']
                for key in ['access_token', 'refresh_token', 'expires_in'])
        ):
            raise MIoTOauthError(f'invalid http response, {http_res.text}')

        return {
            **res_obj['result'],
            'expires_ts': int(
                time.time() +
                (res_obj['result'].get('expires_in', 0)*TOKEN_EXPIRES_TS_RATIO))
        }

    def get_access_token(self, code: str) -> dict:
        """get access token by authorization code

        Args:
            code (str): auth code

        Returns:
            str: _description_
        """
        if not isinstance(code, str):
            raise MIoTOauthError('invalid code')

        return self._get_token(data={
            'client_id': self._client_id,
            'redirect_uri': self._redirect_url,
            'code': code,
        })

    async def get_access_token_async(self, code: str) -> dict:
        return await self.__call_async(partial(self.get_access_token, code))

    def refresh_access_token(self, refresh_token: str) -> dict:
        """get access token  by refresh token.

        Args:
            refresh_token (str): refresh_token

        Returns:
            str: _description_
        """
        if not isinstance(refresh_token, str):
            raise MIoTOauthError('invalid refresh_token')

        return self._get_token(data={
            'client_id': self._client_id,
            'redirect_uri': self._redirect_url,
            'refresh_token': refresh_token,
        })

    async def refresh_access_token_async(self, refresh_token: str) -> dict:
        return await self.__call_async(
            partial(self.refresh_access_token, refresh_token))


class MIoTHttpClient:
    """MIoT http client."""
    GET_PROP_AGGREGATE_INTERVAL: float = 0.2
    GET_PROP_MAX_REQ_COUNT = 150
    _main_loop: asyncio.AbstractEventLoop
    _host: str
    _base_url: str
    _client_id: str
    _access_token: str

    _get_prop_timer: asyncio.TimerHandle
    _get_prop_list: dict[str, dict[str, asyncio.Future | str | bool]]

    def __init__(
            self, cloud_server: str, client_id: str, access_token: str,
            loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> None:
        self._main_loop = loop or asyncio.get_running_loop()
        self._host = None
        self._base_url = None
        self._client_id = None
        self._access_token = None

        self._get_prop_timer: asyncio.TimerHandle = None
        self._get_prop_list = {}

        if (
            not isinstance(cloud_server, str)
            or not isinstance(client_id, str)
            or not isinstance(access_token, str)
        ):
            raise MIoTHttpError('invalid params')

        self.update_http_header(
            cloud_server=cloud_server, client_id=client_id,
            access_token=access_token)

    async def __call_async(self, func) -> any:
        if self._main_loop is None:
            raise MIoTHttpError('miot http, un-support async methods')
        return await self._main_loop.run_in_executor(executor=None, func=func)

    def update_http_header(
        self, cloud_server: Optional[str] = None,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None
    ) -> None:
        if isinstance(cloud_server, str):
            if cloud_server == 'cn':
                self._host = DEFAULT_OAUTH2_API_HOST
            else:
                self._host = f'{cloud_server}.{DEFAULT_OAUTH2_API_HOST}'
            self._base_url = f'https://{self._host}'
        if isinstance(client_id, str):
            self._client_id = client_id
        if isinstance(access_token, str):
            self._access_token = access_token

    @property
    def __api_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            'Host': self._host,
            'X-Client-BizId': 'haapi',
            'Content-Type': 'application/json',
            'Authorization': f'Bearer{self._access_token}',
            'X-Client-AppId': self._client_id,
        })
        return session

    def mihome_api_get(
        self, url_path: str, params: dict,
        timeout: int = MIHOME_HTTP_API_TIMEOUT
    ) -> dict:
        http_res = None
        with self.__api_session as session:
            http_res = session.get(
                url=f'{self._base_url}{url_path}',
                params=params,
                timeout=timeout)
        if http_res.status_code == 401:
            raise MIoTHttpError(
                'mihome api get failed, unauthorized(401)',
                MIoTErrorCode.CODE_HTTP_INVALID_ACCESS_TOKEN)
        if http_res.status_code != 200:
            raise MIoTHttpError(
                f'mihome api get failed, {http_res.status_code}, '
                f'{url_path}, {params}')
        res_obj: dict = http_res.json()
        if res_obj.get('code', None) != 0:
            raise MIoTHttpError(
                f'invalid response code, {res_obj.get("code",None)}, '
                f'{res_obj.get("message","")}')
        _LOGGER.debug(
            'mihome api get, %s%s, %s -> %s',
            self._base_url, url_path, params, res_obj)
        return res_obj

    def mihome_api_post(
        self, url_path: str, data: dict,
        timeout: int = MIHOME_HTTP_API_TIMEOUT
    ) -> dict:
        encoded_data = None
        if data:
            encoded_data = json.dumps(data).encode('utf-8')
        http_res = None
        with self.__api_session as session:
            http_res = session.post(
                url=f'{self._base_url}{url_path}',
                data=encoded_data,
                timeout=timeout)
        if http_res.status_code == 401:
            raise MIoTHttpError(
                'mihome api get failed, unauthorized(401)',
                MIoTErrorCode.CODE_HTTP_INVALID_ACCESS_TOKEN)
        if http_res.status_code != 200:
            raise MIoTHttpError(
                f'mihome api post failed, {http_res.status_code}, '
                f'{url_path}, {data}')
        res_obj: dict = http_res.json()
        if res_obj.get('code', None) != 0:
            raise MIoTHttpError(
                f'invalid response code, {res_obj.get("code",None)}, '
                f'{res_obj.get("message","")}')
        _LOGGER.debug(
            'mihome api post, %s%s, %s -> %s',
            self._base_url, url_path, data, res_obj)
        return res_obj

    def get_user_info(self) -> dict:
        http_res = requests.get(
            url='https://open.account.xiaomi.com/user/profile',
            params={'clientId': self._client_id,
                    'token': self._access_token},
            headers={'content-type': 'application/x-www-form-urlencoded'},
            timeout=MIHOME_HTTP_API_TIMEOUT
        )

        res_obj = http_res.json()
        if (
            not res_obj
            or res_obj.get('code', None) != 0
            or 'data' not in res_obj
            or 'miliaoNick' not in res_obj['data']
        ):
            raise MIoTOauthError(f'invalid http response, {http_res.text}')

        return res_obj['data']

    async def get_user_info_async(self) -> dict:
        return await self.__call_async(partial(self.get_user_info))

    def get_central_cert(self, csr: str) -> Optional[str]:
        if not isinstance(csr, str):
            raise MIoTHttpError('invalid params')

        res_obj: dict = self.mihome_api_post(
            url_path='/app/v2/ha/oauth/get_central_crt',
            data={
                'csr': str(base64.b64encode(csr.encode('utf-8')), 'utf-8')
            }
        )
        if 'result' not in res_obj:
            raise MIoTHttpError('invalid response result')
        cert: str = res_obj['result'].get('cert', None)
        if not isinstance(cert, str):
            raise MIoTHttpError('invalid cert')

        return cert

    async def get_central_cert_async(self, csr: str) -> Optional[str]:
        return await self.__call_async(partial(self.get_central_cert, csr))

    def __get_dev_room_page(self, max_id: str = None) -> dict:
        res_obj = self.mihome_api_post(
            url_path='/app/v2/homeroom/get_dev_room_page',
            data={
                'start_id': max_id,
                'limit': 150,
            },
        )
        if 'result' not in res_obj and 'info' not in res_obj['result']:
            raise MIoTHttpError('invalid response result')
        home_list: dict = {}
        for home in res_obj['result']['info']:
            if 'id' not in home:
                _LOGGER.error(
                    'get dev room page error, invalid home, %s', home)
                continue
            home_list[str(home['id'])] = {'dids': home.get(
                'dids', None) or [], 'room_info': {}}
            for room in home.get('roomlist', []):
                if 'id' not in room:
                    _LOGGER.error(
                        'get dev room page error, invalid room, %s', room)
                    continue
                home_list[str(home['id'])]['room_info'][str(room['id'])] = {
                    'dids': room.get('dids', None) or []}
        if (
            res_obj['result'].get('has_more', False)
            and isinstance(res_obj['result'].get('max_id', None), str)
        ):
            next_list = self.__get_dev_room_page(
                max_id=res_obj['result']['max_id'])
            for home_id, info in next_list.items():
                home_list.setdefault(home_id, {'dids': [], 'room_info': {}})
                home_list[home_id]['dids'].extend(info['dids'])
                for room_id, info in info['room_info'].items():
                    home_list[home_id]['room_info'].setdefault(
                        room_id, {'dids': []})
                    home_list[home_id]['room_info'][room_id]['dids'].extend(
                        info['dids'])

        return home_list

    def get_homeinfos(self) -> dict:
        res_obj = self.mihome_api_post(
            url_path='/app/v2/homeroom/gethome',
            data={
                'limit': 150,
                'fetch_share': True,
                'fetch_share_dev': True,
                'plat_form': 0,
                'app_ver': 9,
            },
        )
        if 'result' not in res_obj:
            raise MIoTHttpError('invalid response result')

        uid: str = None
        home_infos: dict = {}
        for device_source in ['homelist', 'share_home_list']:
            home_infos.setdefault(device_source, {})
            for home in res_obj['result'].get(device_source, []):
                if (
                    'id' not in home
                    or 'name' not in home
                    or 'roomlist' not in home
                ):
                    continue
                if uid is None and device_source == 'homelist':
                    uid = str(home['uid'])
                home_infos[device_source][home['id']] = {
                    'home_id': home['id'],
                    'home_name': home['name'],
                    'city_id': home.get('city_id', None),
                    'longitude': home.get('longitude', None),
                    'latitude': home.get('latitude', None),
                    'address': home.get('address', None),
                    'dids': home.get('dids', []),
                    'room_info': {
                        room['id']: {
                            'room_id': room['id'],
                            'room_name': room['name'],
                            'dids': room.get('dids', [])
                        }
                        for room in home.get('roomlist', [])
                    },
                    'group_id': calc_group_id(
                        uid=home['uid'], home_id=home['id']),
                    'uid': str(home['uid'])
                }
            home_infos['uid'] = uid
        if (
            res_obj['result'].get('has_more', False)
            and isinstance(res_obj['result'].get('max_id', None), str)
        ):
            more_list = self.__get_dev_room_page(
                max_id=res_obj['result']['max_id'])
            for home_id, info in more_list.items():
                if home_id not in home_infos['homelist']:
                    _LOGGER.info('unknown home, %s, %s', home_id, info)
                    continue
                home_infos['homelist'][home_id]['dids'].extend(info['dids'])
                for room_id, info in info['room_info'].items():
                    home_infos['homelist'][home_id]['room_info'].setdefault(
                        room_id, {'dids': []})
                    home_infos['homelist'][home_id]['room_info'][
                        room_id]['dids'].extend(info['dids'])

        return {
            'uid': uid,
            'home_list': home_infos.get('homelist', {}),
            'share_home_list': home_infos.get('share_home_list', [])
        }

    async def get_homeinfos_async(self) -> dict:
        return await self.__call_async(self.get_homeinfos)

    def get_uid(self) -> str:
        return self.get_homeinfos().get('uid', None)

    async def get_uid_async(self) -> str:
        return (await self.get_homeinfos_async()).get('uid', None)

    def __get_device_list_page(
        self, dids: list[str], start_did: str = None
    ) -> dict[str, dict]:
        req_data: dict = {
            'limit': 200,
            'get_split_device': True,
            'dids': dids
        }
        if start_did:
            req_data['start_did'] = start_did
        device_infos: dict = {}
        res_obj = self.mihome_api_post(
            url_path='/app/v2/home/device_list_page',
            data=req_data
        )
        if 'result' not in res_obj:
            raise MIoTHttpError('invalid response result')
        res_obj = res_obj['result']

        for device in res_obj.get('list', []) or []:
            did = device.get('did', None)
            name = device.get('name', None)
            urn = device.get('spec_type', None)
            model = device.get('model', None)
            if did is None or name is None or urn is None or model is None:
                _LOGGER.error(
                    'get_device_list, cloud, invalid device, %s', device)
                continue
            device_infos[did] = {
                'did': did,
                'uid': device.get('uid', None),
                'name': name,
                'urn': urn,
                'model': model,
                'connect_type': device.get('pid', -1),
                'token': device.get('token', None),
                'online': device.get('isOnline', False),
                'icon': device.get('icon', None),
                'parent_id': device.get('parent_id', None),
                'manufacturer': model.split('.')[0],
                # 2: xiao-ai, 1: general speaker
                'voice_ctrl': device.get('voice_ctrl', 0),
                'rssi': device.get('rssi', None),
                'owner': device.get('owner', None),
                'pid': device.get('pid', None),
                'local_ip': device.get('local_ip', None),
                'ssid': device.get('ssid', None),
                'bssid': device.get('bssid', None),
                'order_time': device.get('orderTime', 0),
                'fw_version': device.get('extra', {}).get(
                    'fw_version', 'unknown'),
            }
            if isinstance(device.get('extra', None), dict) and device['extra']:
                device_infos[did]['fw_version'] = device['extra'].get(
                    'fw_version', None)
                device_infos[did]['mcu_version'] = device['extra'].get(
                    'mcu_version', None)
                device_infos[did]['platform'] = device['extra'].get(
                    'platform', None)

        next_start_did = res_obj.get('next_start_did', None)
        if res_obj.get('has_more', False) and next_start_did:
            device_infos.update(self.__get_device_list_page(
                dids=dids, start_did=next_start_did))

        return device_infos

    async def get_devices_with_dids_async(
        self, dids: list[str]
    ) -> dict[str, dict]:
        results: list[dict[str, dict]] = await asyncio.gather(
            *[self.__call_async(
                partial(self.__get_device_list_page, dids[index:index+150]))
                for index in range(0, len(dids), 150)])
        devices = {}
        for result in results:
            if result is None:
                return None
            devices.update(result)
        return devices

    async def get_devices_async(
        self, home_ids: list[str] = None
    ) -> dict[str, dict]:
        homeinfos = await self.get_homeinfos_async()
        homes: dict[str, dict[str, any]] = {}
        devices: dict[str, dict] = {}
        for device_type in ['home_list', 'share_home_list']:
            homes.setdefault(device_type, {})
            for home_id, home_info in (homeinfos.get(
                    device_type, None) or {}).items():
                if isinstance(home_ids, list) and home_id not in home_ids:
                    continue
                homes[device_type].setdefault(
                    home_id, {
                        'home_name': home_info['home_name'],
                        'uid': home_info['uid'],
                        'group_id': home_info['group_id'],
                        'room_info': {}
                    })
                devices.update({did: {
                    'home_id': home_id,
                    'home_name': home_info['home_name'],
                    'room_id': home_id,
                    'room_name': home_info['home_name'],
                    'group_id': home_info['group_id']
                } for did in home_info.get('dids', [])})
                for room_id, room_info in home_info.get('room_info').items():
                    homes[device_type][home_id]['room_info'][
                        room_id] = room_info['room_name']
                    devices.update({
                        did: {
                            'home_id': home_id,
                            'home_name': home_info['home_name'],
                            'room_id': room_id,
                            'room_name': room_info['room_name'],
                            'group_id': home_info['group_id']
                        } for did in room_info.get('dids', [])})
        dids = sorted(list(devices.keys()))
        results: dict[str, dict] = await self.get_devices_with_dids_async(
            dids=dids)
        for did in dids:
            if did not in results:
                devices.pop(did, None)
                _LOGGER.error('get device info failed, %s', did)
                continue
            devices[did].update(results[did])
            # Whether sub devices
            match_str = re.search(r'\.s\d+$', did)
            if not match_str:
                continue
            device = devices.pop(did, None)
            parent_did = did.replace(match_str.group(), '')
            if parent_did in devices:
                devices[parent_did].setdefault('sub_devices', {})
                devices[parent_did]['sub_devices'][match_str.group()[
                    1:]] = device
            else:
                _LOGGER.error(
                    'unknown sub devices, %s, %s', did, parent_did)
        return {
            'uid': homeinfos['uid'],
            'homes': homes,
            'devices': devices
        }

    def get_props(self, params: list) -> list:
        """
        params = [{"did": "xxxx", "siid": 2, "piid": 1},
                    {"did": "xxxxxx", "siid": 2, "piid": 2}]
        """
        res_obj = self.mihome_api_post(
            url_path='/app/v2/miotspec/prop/get',
            data={
                'datasource': 1,
                'params': params
            },
        )
        if 'result' not in res_obj:
            raise MIoTHttpError('invalid response result')
        return res_obj['result']

    async def get_props_async(self, params: list) -> list:
        return await self.__call_async(partial(self.get_props, params))

    def get_prop(self, did: str, siid: int, piid: int) -> any:
        results = self.get_props(
            params=[{'did': did, 'siid': siid, 'piid': piid}])
        if not results:
            return None
        result = results[0]
        if 'value' not in result:
            return None
        return result['value']

    async def __get_prop_handler(self) -> bool:
        props_req: set[str] = set()
        props_buffer: list[dict] = []

        for key, item in self._get_prop_list.items():
            if item.get('tag', False):
                continue
            # NOTICE: max req prop
            if len(props_req) >= self.GET_PROP_MAX_REQ_COUNT:
                break
            item['tag'] = True
            props_buffer.append(item['param'])
            props_req.add(key)

        if not props_buffer:
            _LOGGER.error('get prop error, empty request list')
            return False
        results = await self.__call_async(partial(self.get_props, props_buffer))

        for result in results:
            if not all(
                    key in result for key in ['did', 'siid', 'piid', 'value']):
                continue
            key = f'{result["did"]}.{result["siid"]}.{result["piid"]}'
            prop_obj = self._get_prop_list.pop(key, None)
            if prop_obj is None:
                _LOGGER.error('get prop error, key not exists, %s', result)
                continue
            prop_obj['fut'].set_result(result['value'])
            props_req.remove(key)

        for key in props_req:
            prop_obj = self._get_prop_list.pop(key, None)
            if prop_obj is None:
                continue
            prop_obj['fut'].set_result(None)
        if props_req:
            _LOGGER.error(
                'get prop from cloud failed, %s, %s', len(key), props_req)

        if self._get_prop_list:
            self._get_prop_timer = self._main_loop.call_later(
                self.GET_PROP_AGGREGATE_INTERVAL,
                lambda: self._main_loop.create_task(
                    self.__get_prop_handler()))
        else:
            self._get_prop_timer = None
        return True

    async def get_prop_async(
        self, did: str, siid: int, piid: int, immediately: bool = False
    ) -> any:
        if immediately:
            return await self.__call_async(
                partial(self.get_prop, did, siid, piid))
        key: str = f'{did}.{siid}.{piid}'
        prop_obj = self._get_prop_list.get(key, None)
        if prop_obj:
            return await prop_obj['fut']
        fut = self._main_loop.create_future()
        self._get_prop_list[key] = {
            'param': {'did': did, 'siid': siid, 'piid': piid},
            'fut': fut
        }
        if self._get_prop_timer is None:
            self._get_prop_timer = self._main_loop.call_later(
                self.GET_PROP_AGGREGATE_INTERVAL,
                lambda: self._main_loop.create_task(
                    self.__get_prop_handler()))

        return await fut

    def set_prop(self, params: list) -> list:
        """
        params = [{"did": "xxxx", "siid": 2, "piid": 1, "value": False}]
        """
        res_obj = self.mihome_api_post(
            url_path='/app/v2/miotspec/prop/set',
            data={
                'params': params
            },
            timeout=15
        )
        if 'result' not in res_obj:
            raise MIoTHttpError('invalid response result')

        return res_obj['result']

    async def set_prop_async(self, params: list) -> list:
        """
        params = [{"did": "xxxx", "siid": 2, "piid": 1, "value": False}]
        """
        return await self.__call_async(partial(self.set_prop, params))

    def action(
        self, did: str, siid: int, aiid: int, in_list: list[dict]
    ) -> dict:
        """
        params = {"did": "xxxx", "siid": 2, "aiid": 1, "in": []}
        """
        # NOTICE: Non-standard action param
        res_obj = self.mihome_api_post(
            url_path='/app/v2/miotspec/action',
            data={
                'params': {
                    'did': did,
                    'siid': siid,
                    'aiid': aiid,
                    'in': [item['value'] for item in in_list]}
            },
            timeout=15
        )
        if 'result' not in res_obj:
            raise MIoTHttpError('invalid response result')

        return res_obj['result']

    async def action_async(
        self, did: str, siid: int, aiid: int, in_list: list[dict]
    ) -> dict:
        return await self.__call_async(
            partial(self.action, did, siid, aiid, in_list))