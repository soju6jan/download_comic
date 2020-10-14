# -*- coding: utf-8 -*-
#########################################################
# python
import os, sys, traceback, re, json, threading, time, shutil
from datetime import datetime
# third-party
import requests
# third-party
from flask import request, render_template, jsonify
from sqlalchemy import or_, and_, func, not_, desc
import lxml.html
# sjva 공용
from framework import db, scheduler, path_data, socketio
from framework.util import Util
from framework.common.util import headers, get_json_with_auth_session
from framework.common.plugin import LogicModuleBase, default_route_socketio
# 패키지
from .plugin import P
from .comic_queue import ComicQueue, ComicQueueEntity
logger = P.logger
package_name = P.package_name
ModelSetting = P.ModelSetting
#########################################################

define = {
    'manatoki': {
        'site_default' : 'https://manatoki81.net',
        'kor_name' : u'마나토끼',
        'url_prefix' : 'comic',
    },
    'newtoki': {
        'site_default' : 'https://newtoki81.com',
        'kor_name' : u'뉴토끼',
        'url_prefix' : 'webtoon',
    },
}

def get_logic(module_name, ModelItem, ComicQueueEntityModule):
    class LogicToki(LogicModuleBase):
        db_default = {
            '{}_db_version'.format(module_name) : '1',
            '{}_url'.format(module_name) : define[module_name]['site_default'],
            '{}_current_code'.format(module_name) : '',
            '{}_order_desc'.format(module_name) : 'True',
            '{}_max_queue_count'.format(module_name) : '4',
            '{}_download_folder'.format(module_name) : os.path.join(path_data, P.package_name, module_name),
            '{}_make_series_folder'.format(module_name) : 'True',
            '{}_use_zip'.format(module_name) : 'True', 
            '{}_list_last_option'.format(module_name) : '',
            '{}_auto_start'.format(module_name) : 'False',
            '{}_interval'.format(module_name) : '180',
            '{}_auto_code_list'.format(module_name) : '',
            '{}_auto_black_code_list'.format(module_name) : '',
            '{}_all_download'.format(module_name) : 'False',
            '{}_queue_auto_clear'.format(module_name) : 'False',
        }

        def __init__(self, P):
            super(LogicToki, self).__init__(P, 'setting', scheduler_desc='{} 자동 다운로드'.format(define[module_name]['kor_name']))
            self.name = module_name
            self.current_data = None
            default_route_socketio(P, self)

        def process_menu(self, sub, req):
            arg = P.ModelSetting.to_dict()
            arg['sub'] = self.name
            for key, value in arg.items():
                if key.startswith(self.name):
                    arg[key.replace(self.name+'_','')] = value
                    del arg[key]
            if sub in ['setting', 'request', 'queue', 'list']:
                if sub == 'setting':
                    job_id = '%s_%s' % (self.P.package_name, self.name)
                    arg['scheduler'] = str(scheduler.is_include(job_id))
                    arg['is_running'] = str(scheduler.is_running(job_id))
                elif sub == 'request':
                    if sub == 'request' and req.args.get('content_code') is not None:
                        arg['current_code'] = req.args.get('content_code')
               
                return render_template('{package_name}_{module_name}_{sub}.html'.format(package_name=P.package_name, module_name='toki', sub=sub), arg=arg)
            return render_template('sample.html', title='%s - %s' % (P.package_name, sub))

        def process_ajax(self, sub, req):
            try:
                if sub == 'setting_save':
                    for key, value in req.form.items():
                        ModelSetting.set('{}_{}'.format(module_name, key), value)
                    self.queue.set_max_count(ModelSetting.get_int('{}_max_queue_count'.format(module_name)))
                    return jsonify(True)
                # 요청
                if sub == 'analysis':
                    code = request.form['code'].strip()
                    P.ModelSetting.set('{sub}_current_code'.format(sub=self.name), code)
                    if self.current_data is not None and self.current_data['code'] == code:
                        return jsonify({'ret':'success', 'data':self.current_data})
                    if code.startswith('http'):
                        code = code.split('?')[0].split('{}/'.format(define[module_name]['url_prefix']))[1]
                    data = self.get_series_info(code)
                    data['list_order'] = 'desc'
                    if P.ModelSetting.get_bool('{sub}_order_desc'.format(sub=self.name)) == False:
                        data['episodes'] = list(reversed(data['episodes']))
                        data['list_order'] = 'asc'
                    self.current_data = data
                    return jsonify({'ret':'success', 'data':data})
                elif sub == 'add_queue':
                    ret = {}
                    info = json.loads(request.form['data'])
                    ret['ret'] = self.add(info)
                    return jsonify(ret)
                elif sub == 'add_queue_checked_list':
                    data = json.loads(request.form['data'])
                    def func():
                        count = 0
                        for tmp in data:
                            add_ret = self.add(tmp)
                            if add_ret.startswith('enqueue'):
                                self.socketio_callback('list_refresh', '')
                                count += 1
                        notify = {'type':'success', 'msg' : u'%s 개의 에피소드를 큐에 추가 하였습니다.' % count}
                        socketio.emit("notify", notify, namespace='/framework', broadcast=True)
                    thread = threading.Thread(target=func, args=())
                    thread.daemon = True  
                    thread.start()
                    return jsonify('')
                # 큐
                elif sub == 'entity_list':
                    return jsonify(self.queue.get_entity_list())
                elif sub == 'queue_command':
                    ret = self.queue.command(req.form['command'], int(req.form['entity_id']))
                    return jsonify(ret)
                # 목록
                elif sub == 'web_list':
                    return jsonify(ModelItem.web_list(request))
                elif sub == 'db_remove':
                    return jsonify(ModelItem.delete_by_id(req.form['id']))
            except Exception as e: 
                P.logger.error('Exception:%s', e)
                P.logger.error(traceback.format_exc())
                return jsonify({'ret':'exception', 'log':str(e)})

        
        def scheduler_function(self):
            self.restart_incompleted()
            code_list = ModelSetting.get_list('{}_auto_code_list'.format(module_name), ',')
            black_list = ModelSetting.get_list('{}_auto_black_code_list'.format(module_name), ',')
            if len(code_list) == 1 and code_list[0] == 'all':
                code_list = self.get_recent_code_list()
            if code_list is None or len(code_list) == 0:
                return
            for code in code_list:
                if code_list[0] == 'all' and code in black_list:
                    continue
                try:
                    series_info = self.get_series_info(code)
                    if series_info is not None and len(series_info['episodes']) > 0:
                        if ModelSetting.get_bool('{}_all_download'.format(module_name)):
                            for epi in series_info['episodes']:
                                self.add(epi)
                                self.socketio_callback('list_refresh', '')
                        else:
                            self.add(series_info['episodes'][0])
                            self.socketio_callback('list_refresh', '')
                except Exception as e: 
                    P.logger.error('Exception:%s', e)
                    P.logger.error(traceback.format_exc())

        def plugin_load(self):
            self.queue = ComicQueue(self, P.ModelSetting.get_int('{}_max_queue_count'.format(module_name)))
            self.queue.queue_start()
            self.restart_incompleted()
           

        def reset_db(self):
            db.session.query(ModelItem).delete()
            db.session.commit()
            return True
        #########################################################

        def restart_incompleted(self):
            def func():
                data = ModelItem.get_list_incompleted()
                for db_entity in data:
                    add_ret = self.add(db_entity.to_queue_info())
                    #if add_ret.startswith('enqueue'):
                    #    self.socketio_callback('list_refresh', '')
                self.socketio_callback('list_refresh', '')
            thread = threading.Thread(target=func, args=())
            thread.daemon = True  
            thread.start()


        def get_series_info(self, code):
            try:
                url = P.ModelSetting.get('{}_url'.format(module_name)) + '/{}/'.format(define[module_name]['url_prefix']) + code
                root = lxml.html.fromstring(requests.get(url).content) 
                series = {}
                series['code'] = code
                series['title'] = root.xpath('//section[@itemscope]/article/div[1]/div/div/div[2]/div[1]/span/b')[0].text_content().strip()
                try: series['poster'] = root.xpath('//section[@itemscope]/article/div[1]/div/div/div[1]/div/div/img')[0].attrib['src'].strip()
                except: series['poster'] = ''
                try: series['author'] = root.xpath('//section[@itemscope]/article/div[1]/div/div/div[2]/div[2]/a')[0].text_content().strip()
                except: series['author'] = ''
                try: series['genre'] = [x.text_content().strip() for x in root.xpath('//section[@itemscope]/article/div[1]/div/div/div[2]/div[3]/a')]
                except: series['genre'] = []
                series['episodes'] = []
                for item in root.xpath('//ul[@class="list-body"]/li'):
                    episode = {}
                    episode['idx'] = int(item.xpath('div[1]')[0].text_content().strip())
                    episode['title'] = ''.join(item.xpath('div[2]/a/text()')).strip()
                    episode['url'] = item.xpath('div[2]/a')[0].attrib['href'].strip().split('?')[0]
                    match = re.compile(r'%s/(?P<code>.*?)($|/|\?)' % define[module_name]['url_prefix']).search(episode['url'])
                    episode['code'] = match.group('code')
                    episode['series_code'] = code
                    episode['series_title'] = series['title']
                    series['episodes'].append(episode)
                return series
            except Exception as e: 
                logger.debug('URL:%s', url)
                logger.error('Exception:%s', e)
                logger.error(traceback.format_exc())


        def add(self, episode_info):
            if self.queue.is_exist(episode_info, 'code'):
                ret = 'queue_exist'
            else:
                db_entity = ModelItem.get_by_code(episode_info['code'])
                if db_entity is None:
                    entity = ComicQueueEntityModule(P, self, episode_info)
                    self.queue.append(entity)
                    ModelItem.append(entity.as_dict())
                    ret = 'enqueue_db_append'
                elif db_entity.status != 'completed':
                    entity = ComicQueueEntityModule(P, self, episode_info)
                    self.queue.append(entity)
                    ret = 'enqueue_db_exist'
                else:
                    ret = 'db_completed'
            logger.debug('QUEUE ADD : %s %s %s %s', module_name, ret, episode_info['code'], episode_info['title'])
            return ret

        def get_recent_code_list(self):
            if module_name == 'manatoki':
                url = '{}/page/update'.format(ModelSetting.get('{}_url'.format(module_name)))
                root = lxml.html.fromstring(requests.get(url).content) 
                ret = [ t.attrib['href'].split('comic/')[1] for t in root.xpath(u'//a[text()="전편보기"]') ]
            elif module_name == 'newtoki':
                url = '{}/webtoon'.format(ModelSetting.get('{}_url'.format(module_name)))
                root = lxml.html.fromstring(requests.get(url).content)
                ret = [ t.attrib['href'].split('webtoon/')[1].split('/')[0] for t in root.xpath('//div[@class="in-lable trans-bg-black"]/a[contains(@href,"/webtoon/")]') ]
            return ret[:30]
    return LogicToki




def get_queue_entity(module_name, ModelItem):
    class ComicQueueEntityToki(ComicQueueEntity):
        def __init__(self, P, module_logic, info):
            super(ComicQueueEntityToki, self).__init__(P, module_logic, info)

        def refresh_status(self):
            self.module_logic.socketio_callback('status', self.as_dict())

        def download(self):
            completed_flag = False
            try:
                self.set_status(u'분석중')
                url = '{}/{}/{}'.format(ModelSetting.get('{}_url'.format(self.module_logic.name)), define[module_name]['url_prefix'], self.info['code'])
                data = requests.get(url).content
                tmp = ''.join(re.compile(r'html_data\+\=\'(.*?)\'\;').findall(data))
                html = ''.join([chr(int(x, 16)) for x in tmp.rstrip('.').split('.')])
                image_list = re.compile(r'src="/img/loading-image.gif"\sdata\-\w{11}="(.*?)"').findall(html)
                self.total_image_count = len(image_list)
                self.refresh_status()
                download_folder = ModelSetting.get('{}_download_folder'.format(module_name))
                if ModelSetting.get_bool('{}_make_series_folder'.format(module_name)):
                    download_folder = os.path.join(download_folder, Util.change_text_for_use_filename(self.info['series_title']))

                self.savepath = os.path.join(download_folder, Util.change_text_for_use_filename(self.info['title']))
                if not os.path.exists(self.savepath):
                    os.makedirs(self.savepath)
                if ModelSetting.get('{}_use_zip'.format(module_name)) and os.path.exists(self.savepath + '.zip'):
                    self.percent = 100
                    self.set_status(u'파일 있음')
                    self.savepath = self.savepath + '.zip'
                    completed_flag = True
                    return
                self.set_status(u'다운로드중')
                for idx, tmp in enumerate(image_list):
                    filepath = os.path.join(self.savepath, str(idx+1).zfill(3) + '.' + tmp.split('.')[-1])
                    ret = self.image_download(tmp, filepath)
                    # 실패처리
                    if ret == 200:
                        continue
                    else:
                        ret = self.image_download(tmp, filepath)
                        if ret == 200:
                            continue
                        else:
                            self.set_status(u'실패')
                            shutil.rmtree(self.savepath)
                            return
                    self.percent = (int)((idx+1) * 100 / self.total_image_count)
                    self.refresh_status()
                    
                if ModelSetting.get('{}_use_zip'.format(module_name)):
                    self.set_status(u'Zip 생성중')
                    Util.makezip(self.savepath)
                    self.savepath = self.savepath + '.zip'
                self.set_status(u'다운로드 완료')
                completed_flag = True
            except Exception as e: 
                P.logger.error('Exception:%s', e)
                P.logger.error(traceback.format_exc())
                self.set_status(u'실패')
            finally:
                if completed_flag:
                    item = ModelItem.get_by_code(self.info['code'])
                    item.savepath = self.savepath
                    item.status = 'completed'
                    item.completed_time = datetime.now()
                    item.save()
                    if ModelSetting.get_bool('{}_queue_auto_clear'.format(module_name)):
                        self.module_logic.queue.command('delete_completed', -1)
                        self.module_logic.socketio_callback('list_refresh', '')

    return ComicQueueEntityToki




def get_item_model(module_name):
    class ModelItem(db.Model):
        __tablename__ = '{package_name}_{module_name}_item'.format(package_name=P.package_name, module_name=module_name)
        __table_args__ = {'mysql_collate': 'utf8_general_ci'}
        __bind_key__ = P.package_name

        id = db.Column(db.Integer, primary_key=True)
        created_time = db.Column(db.DateTime)
        completed_time = db.Column(db.DateTime)
        series_code = db.Column(db.String)
        series_title = db.Column(db.String)
        code = db.Column(db.String)
        title = db.Column(db.String)
        status = db.Column(db.String)
        savepath = db.Column(db.String)

        def __init__(self):
            self.created_time = datetime.now()
            self.status = 'ready'

        def __repr__(self):
            return repr(self.as_dict())

        def as_dict(self):
            ret = {x.name: getattr(self, x.name) for x in self.__table__.columns}
            ret['created_time'] = self.created_time.strftime('%Y-%m-%d %H:%M:%S') 
            ret['completed_time'] = self.completed_time.strftime('%Y-%m-%d %H:%M:%S') if self.completed_time is not None else None
            return ret

        def save(self):
            db.session.add(self)
            db.session.commit()

        def to_queue_info(self):
            info = {}
            info['title'] = self.title
            info['code'] = self.code
            info['series_code'] = self.series_code
            info['series_title'] = self.series_title
            info['url'] = ModelSetting.get('{}_url'.format(module_name)) + '/' + define[module_name]['url_prefix'] + '/' + info['code']
            return info

        @classmethod
        def get_by_id(cls, id):
            return db.session.query(cls).filter_by(id=id).first()
        
        @classmethod
        def get_by_code(cls, code):
            return db.session.query(cls).filter_by(code=code).first()

        @classmethod
        def delete_by_id(cls, id):
            db.session.query(cls).filter_by(id=id).delete()
            db.session.commit()
            return True

        @classmethod
        def web_list(cls, req):
            ret = {}
            page = int(req.form['page']) if 'page' in req.form else 1
            page_size = 30
            job_id = ''
            search = req.form['search_word'] if 'search_word' in req.form else ''
            option = req.form['option'] if 'option' in req.form else 'all'
            order = req.form['order'] if 'order' in req.form else 'desc'
            query = cls.make_query(search=search, order=order, option=option)
            count = query.count()
            query = query.limit(page_size).offset((page-1)*page_size)
            lists = query.all()
            ret['list'] = [item.as_dict() for item in lists]
            ret['paging'] = Util.get_paging_info(count, page, page_size)
            ModelSetting.set('{}_list_last_option'.format(module_name), '%s|%s|%s|%s' % (option, order, search, page))
            return ret

        @classmethod
        def make_query(cls, search='', order='desc', option='all'):
            query = db.session.query(cls)
            if search is not None and search != '':
                if search.find('|') != -1:
                    tmp = search.split('|')
                    conditions = []
                    for tt in tmp:
                        if tt != '':
                            conditions.append(cls.title.like('%'+tt.strip()+'%') )
                    query = query.filter(or_(*conditions))
                elif search.find(',') != -1:
                    tmp = search.split(',')
                    for tt in tmp:
                        if tt != '':
                            query = query.filter(cls.title.like('%'+tt.strip()+'%'))
                else:
                    query = query.filter(cls.title.like('%'+search+'%'))
            if option == 'completed':
                query = query.filter(cls.status == 'completed')
            elif option == 'incompleted':
                query = query.filter(cls.status != 'completed')
            query = query.order_by(desc(cls.id)) if order == 'desc' else query.order_by(cls.id)
            return query  

        @classmethod
        def get_list_incompleted(cls):
            return db.session.query(cls).filter(cls.status != 'completed').all()

        @classmethod
        def append(cls, q):
            item = cls()
            item.title = q['info']['title']
            item.code = q['info']['code']
            item.series_code = q['info']['series_code']
            item.series_title = q['info']['series_title']
            item.savepath = None 
            item.save()

    return ModelItem
