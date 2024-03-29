# -*- coding: utf-8 -*-
# python
import os, traceback
# third-party
from flask import Blueprint
# sjva 공용
from framework.logger import get_logger
from framework import app, path_data
from framework.util import Util
from plugin import get_model_setting, Logic, default_route, PluginUtil
# 패키지
#########################################################
class P(object):
    package_name = __name__.split('.')[0]
    logger = get_logger(package_name)
    blueprint = Blueprint(package_name, package_name, url_prefix='/%s' %  package_name, template_folder=os.path.join(os.path.dirname(__file__), 'templates'))
    menu = {
        'main' : [package_name, u'만화 다운로드'],
        'sub' : [
            ['manatoki', u'마나토끼'], ['newtoki', u'뉴토끼'], ['log', u'로그']
        ], 
        'category' : 'service',
        'sub2' : {
            'manatoki' : [
                ['setting', u'설정'], ['request', u'요청'], ['queue', u'큐'], ['list', u'목록']
            ],
            'newtoki' : [
                ['setting', u'설정'], ['request', u'요청'], ['queue', u'큐'], ['list', u'목록']
            ],
        }
    }  
    plugin_info = {
        'version' : '0.2.0.0',
        'name' : 'downloader_comic',
        'category_name' : 'service',
        'icon' : '',
        'developer' : 'soju6jan',
        'description' : u'만화 다운로드',
        'home' : 'https://github.com/soju6jan/download_comic',
        'more' : '',
    }
    ModelSetting = get_model_setting(package_name, logger)
    logic = None
    module_list = None
    home_module = 'manatoki'


def initialize():
    try:
        app.config['SQLALCHEMY_BINDS'][P.package_name] = 'sqlite:///%s' % (os.path.join(path_data, 'db', '{package_name}.db'.format(package_name=P.package_name)))
        PluginUtil.make_info_json(P.plugin_info, __file__)

        from .base_logic_toki import get_logic, get_queue_entity, get_item_model
        module_name = 'manatoki'
        ModelItem = get_item_model(module_name)
        LogicManatoki = get_logic(module_name, ModelItem, get_queue_entity(module_name, ModelItem))
        module_name = 'newtoki'
        ModelItem = get_item_model(module_name)
        LogicNewtoki = get_logic(module_name, ModelItem, get_queue_entity(module_name, ModelItem))

        P.module_list = [LogicManatoki(P), LogicNewtoki(P)]
        P.logic = Logic(P)
        default_route(P)
    except Exception as e: 
        P.logger.error('Exception:%s', e)
        P.logger.error(traceback.format_exc())

initialize()

