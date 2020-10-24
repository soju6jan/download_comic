# -*- coding: utf-8 -*-
#########################################################
# python
import os, sys, traceback
import threading, time
from datetime import datetime
import abc
# third-party
import requests
# sjva 공용
from framework import py_queue
#########################################################

class ComicQueueEntity(abc.ABCMeta('ABC', (object,), {'__slots__': ()})):

    def __init__(self, P, module_logic, info):
        self.P = P
        self.module_logic = module_logic
        self.entity_id = -1
        self.status = u'대기'
        self.info = info
        self.cancel = False
        self.created_time = datetime.now().strftime('%m-%d %H:%M:%S')
        self.savepath = None
        self.filename = None
        self.total_image_count = 0
        self.current_image_count = 0
        self.percent = 0


    @abc.abstractmethod
    def refresh_status(self):
        pass

    @abc.abstractmethod
    def download(self):
        pass

    def as_dict(self):
        tmp = {}
        tmp['entity_id'] = self.entity_id
        tmp['status'] = self.status
        tmp['cancel'] = self.cancel
        tmp['created_time'] = self.created_time#.strftime('%m-%d %H:%M:%S') 
        tmp['savepath'] = self.savepath
        tmp['filename'] = self.filename
        tmp['total_image_count'] = self.total_image_count
        tmp['current_image_count'] = self.current_image_count
        tmp['percent'] = self.percent
        tmp['info'] = self.info

        return tmp

    def image_download(self, url, image_filepath):
        try:
            headers = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/68.0.3440.106 Safari/537.36"}
            image_data = requests.get(url, headers=headers, stream=True)
            data = image_data.content
            with open(image_filepath, 'wb') as handler:
                handler.write(data)
            return image_data.status_code
        except Exception as e:
            self.P.logger.error('Exception:%s', e)
            self.P.logger.error(traceback.format_exc())

    def set_status(self, status):
        self.status = status
        self.refresh_status()


class ComicQueue(object):

    def __init__(self, module_logic, max_count):
        self.P = module_logic.P
        self.module_logic = module_logic
        self.max_count = max_count
        self.current_count = 0
        self.download_queue = None
        self.download_thread = None
        self.entity_list = []
        self.entity_index = 1

    def queue_start(self):
        try:
            if self.download_queue is None:
                self.download_queue = py_queue.Queue()
            if self.download_thread is None:
                self.download_thread = threading.Thread(target=self.download_thread_function, args=())
                self.download_thread.daemon = True  
                self.download_thread.start()
        except Exception as e: 
            self.P.logger.error('Exception:%s', e)
            self.P.logger.error(traceback.format_exc())


    def download_thread_function(self):
        while True:
            try:
                while True:
                    if self.current_count < self.max_count:
                        break
                    time.sleep(5)
                entity = self.download_queue.get()
                if entity.cancel:
                    self.download_queue.task_done()
                    continue
                
                self.current_count += 1
                def func():
                    try:
                        entity.download()
                    finally:
                        self.current_count += -1
                thread = threading.Thread(target=func, args=())
                thread.daemon = True  
                thread.start()
                self.download_queue.task_done()    
            except Exception as e: 
                self.P.logger.error('Exception:%s', e)
                self.P.logger.error(traceback.format_exc())

    
    #def add_queue(self, entity):
     #   self.download_queue.put(entity)
    
    def set_max_count(self, max_count):
        self.max_count = max_count

    def get_max_count(self):
        return self.max_count

    def command(self, command, entity_id):
        self.P.logger.debug('command :%s %s', command, entity_id)
        ret = {}
        if command == 'cancel':
            entity = self.get_entity_by_entity_id(entity_id)
            if entity is not None:
                if entity.status == u'대기':
                    entity.cancel = True
                    entity.status = u'취소'
                    ret['ret'] = 'refresh'
                    entity.refresh_status()
                else:
                    entity.refresh_status()
                    ret['ret'] = 'refresh'
        elif command == 'reset':
            if self.download_queue is not None:
                with self.download_queue.mutex:
                    self.download_queue.queue.clear()
            self.entity_list = []
            ret['ret'] = 'refresh'
        elif command == 'delete_completed':
            new_list = []
            for _ in self.entity_list:
                if _.status in [u'파일 있음', u'취소', u'다운로드 완료']:
                    continue
                new_list.append(_)
            self.entity_list = new_list
            ret['ret'] = 'refresh'
        return ret


    def append(self, entity):
        entity.entity_id = self.entity_index
        self.entity_index += 1
        self.entity_list.append(entity)
        self.download_queue.put(entity)


    def is_exist(self, info, key):
        for e in self.entity_list:
            if e.info[key] == info[key]:
                return True
        return False
     

    def get_entity_by_entity_id(self, entity_id):
        for _ in self.entity_list:
            if _.entity_id == entity_id:
                return _
        return None

    def get_entity_list(self):
        ret = []
        for x in self.entity_list:
            tmp = x.as_dict()
            ret.append(tmp)
        return ret
