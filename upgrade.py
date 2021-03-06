#!/usr/bin/python
# Copyright (c) 2011 Gluster, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os, ast
import simplejson as json
import pickle
import pdb
from xattr import getxattr, setxattr
from hashlib import md5
from tempfile import mkdtemp
from shutil import copyfile, rmtree
from swift.common.utils import HASH_PATH_SUFFIX

PICKLE_PROTOCOL = 2
METADATA_KEY = 'user.swift.metadata'
AUTH_ACCOUNT = ''
ADMIN_URL = ''
ADMIN_KEY = ''
TMP_DIR = ''

def restore_user_data():
    print 'Upgrading data..'
    cmd = 'gluster-object-prep -K %s -A %s' %(ADMIN_KEY, ADMIN_URL)
    if os.system(cmd + '>>/dev/null'):
        raise Exception('%s failed, aborting upgrade.' %cmd)

    fp = file("user_info", 'r+')

    while True:
       user_data = fp.readline()
       user_data = user_data.strip('\n')
       cmd = 'gluster-object-add-user -K %s -A %s %s' %(ADMIN_KEY, ADMIN_URL,
                                                         user_data)
       if len(user_data) == 0:
               break
       if os.system(cmd + '>>/dev/null'):
           raise Exception('%s failed' %cmd)
    fp.close()


def get_account_list():
    acc_list = []
    cmnd = 'gluster volume info'

    if os.system(cmnd + ' >> /dev/null'):
        raise Exception('Getting volume info failed')
        return acc_list

    fp = os.popen(cmnd)
    while True:
        item = fp.readline()
        if not item:
            break
        item = item.strip('\n').strip(' ')
        if item.lower().startswith('volume name:'):
            acc_list.append(item.split(':')[1].strip(' '))

    fp.close()

    return acc_list


def get_user_data(acc, user):
    fp = file(os.path.join(AUTH_ACCOUNT, acc, user))
    user_dt = fp.readline()
    user_dt = user_dt.strip('\n')
    fp.close()
    return ast.literal_eval(user_dt)


def store_user_info():
    fpi = file('user_info', 'w+')
    accounts = get_account_list()
    print 'Retrieving data..'
    for acc in accounts:
        if os.path.isdir(os.path.join(AUTH_ACCOUNT, acc)):
            for user in os.listdir(os.path.join(AUTH_ACCOUNT, acc)):
                if user != '.services':
                    user_data = {}
                    user_data = get_user_data(acc, user)
                    user_details = ''
                    for dict in user_data['groups']:
                        if dict['name'] == '.admin':
                            user_details += '-a '
                        if dict['name'] == '.reseller_admin':
                            user_details += '-r '
                    if int(user_data['uid']) == -1:
                        user_data['uid'] = ''
                    if int(user_data['gid']) == -1:
                        user_data['gid'] = ''
                    user_details = user_details + ' '.join([acc, user,
                                    user_data['auth'].replace('plaintext:', ''),
                                    user_data['uid'], user_data['gid']])
                    fpi.writelines(user_details + '\n')
    fpi.close()


def read_metadata(path):
    """
Helper function to read the pickled metadata from a File/Directory .

:param path: File/Directory to read metadata from.

:returns: dictionary of metadata
"""
    metadata = ''
    key = 0
    try:
        while True:
            metadata += getxattr(path, '%s%s' % (METADATA_KEY, (key or '')))
            key += 1
    except IOError:
        pass
    if metadata:
        return pickle.loads(metadata)
    else:
        return metadata

def write_metadata(path, metadata):
    """
Helper function to write pickled metadata for a File/Directory.

:param path: File/Directory path to write the metadata
:param metadata: metadata to write
"""
    metastr = pickle.dumps(metadata, PICKLE_PROTOCOL)
    key = 0
    while metastr:
        setxattr(path, '%s%s' % (METADATA_KEY, key or ''), metastr[:254])
        metastr = metastr[254:]
        key += 1

def copy_metadata(src, dst):
    write_metadata(dst, read_metadata(src))


def dup_dir_tree(src, dst, mask_dir=''):

    copy_metadata(src, dst)

    for (path, dirs, files) in os.walk(src):
        obj_path = path.replace(src, '').strip('/')
        if mask_dir:
            dirs.remove(mask_dir)
            mask_dir = ''
        for i in dirs:
            os.mkdir(os.path.join(dst, obj_path, i))
            copy_metadata(os.path.join(src, obj_path, i),
                          os.path.join(dst, obj_path, i))

        for i in files:
            copyfile(os.path.join(src, obj_path, i),
                     os.path.join(dst, obj_path, i))
            copy_metadata(os.path.join(src, obj_path, i),
                          os.path.join(dst, obj_path, i))

def revert_changes():
    dup_dir_tree(TMP_DIR, AUTH_ACCOUNT)
    rmtree(TMP_DIR)

def clear_existing_data():
    global TMP_DIR

    TMP_DIR = mkdtemp(dir=AUTH_ACCOUNT)
    dup_dir_tree(AUTH_ACCOUNT, TMP_DIR, TMP_DIR.replace(AUTH_ACCOUNT, '').strip('/'))

    for (path, dirs, files) in os.walk(AUTH_ACCOUNT):
        dirs.remove(TMP_DIR.replace(AUTH_ACCOUNT, '').strip('/'))
        for i in dirs:
            rmtree(os.path.join(AUTH_ACCOUNT, i))
        break

def unmount_tmp_dir():
    print 'Cleaning up temporary files and directories'

    mnt_cmd = 'umount ' + AUTH_ACCOUNT

    if os.system(mnt_cmd):
        raise Exception('Unount failed on %s' % (AUTH_ACCOUNT))

    os.rmdir(AUTH_ACCOUNT)


def encrypt(name):
    return md5(name + HASH_PATH_SUFFIX).hexdigest()


def upgrade_url(url):
    import re
    obj = re.match('(https://.*/v1/AUTH_)(.*)', url)
    if obj == None or (obj.groups()[1] not in get_account_list()):
        raise Exception('failed to upgrade services')
    else:
        return obj.groups()[0] + encrypt(obj.groups()[1])


def restore_service_files():
    print 'Restoring the services..'
    accounts = get_account_list()
    for acc in accounts:
        if os.path.isdir(os.path.join(TMP_DIR, acc)):
            try:
                fp = file(os.path.join(TMP_DIR, acc, '.services'), 'r+')
                data = fp.readline()
                data = data.strip('\n')
                data_dict = ast.literal_eval(data)
                data_dict['storage']['local'] = upgrade_url(
                                                data_dict['storage']['local'])
                fp.close()

                fp = file(os.path.join(AUTH_ACCOUNT, acc, '.services'), 'w+')
                fp.write(json.dumps(data_dict))
                fp.close()
            except IOError:
                pass

def init():
    global AUTH_ACCOUNT
    global ADMIN_URL
    global ADMIN_KEY

    ADMIN_URL = raw_input('Enter the ADMIN_URL(Press Enter for default https://127.0.0.1:443/auth):')
    if not ADMIN_URL:
        ADMIN_URL = 'https://127.0.0.1:443/auth'
    ADMIN_KEY = raw_input('Enter the ADMIN_KEY:')
    AUTH_ACCOUNT = mkdtemp(dir='/tmp')

    mnt_cmd = 'mount -t glusterfs localhost:/auth %s' %AUTH_ACCOUNT
    if os.system(mnt_cmd):
        raise Exception('Mount failed on %s' % (AUTH_ACCOUNT))
        exit(1)


def main():
    print 'This script will upgrade auth info of Beta1 to Beta2,' \
          ' continue only if you were using Beta1 and installed Beta2. \n'\
          'You should be using the same auth volume for using the' \
          ' existing auth data. In case of any failure, old auth data \n' \
          'will be reverted back and you are supposed to clean and recreate it manually.'
    proceed = raw_input('Press c to continue:')
    if proceed != 'c':
        exit(1)
    init()
    store_user_info()
    clear_existing_data()
    try:
        restore_user_data()
        restore_service_files()
        print 'Successfully upgraded auth data to Beta2.'
    except Exception as inst:
        print inst.args
        print 'Reverting the changes, retry or do manual upgrade.'
        revert_changes()
    finally:
        unmount_tmp_dir()


if __name__ == '__main__':
    main()

