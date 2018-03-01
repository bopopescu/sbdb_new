import datetime

from celery import shared_task
# from django.contrib.auth.models import User
from accounts.models import UserInfo
from django.core.mail import EmailMultiAlternatives
from django.template import loader
from django.core import serializers
from dbmanage.myapp.include import inception as incept, binlog2sql
from dbmanage.myapp.include.encrypt import prpcrypt
from dbmanage.myapp.models import User_profile, Db_instance, Task, Incep_error_log, Db_account

from adminset.settings import   EMAIL_SENDER
from django.utils import timezone

from django_celery_beat.models import CrontabSchedule, PeriodicTask

import json


@shared_task()
def d(x,y):
    return x*y

@shared_task()
def process_runtask(task_id):
    mytask = Task.objects.get(id=task_id)
    instance = Db_instance.objects.get(ip=mytask.instance.split(':')[0], port=mytask.instance.split(':')[1])
    db_account = Db_account.objects.get(instance=instance, db_account_role=mytask.db_account_role)
    sqltext = mytask.sqltext
    tar_dbname = mytask.dbtag
    flag = (1 if mytask.backup_status == 1 else 3)
    status = 'running'
    mytask.status = status
    mytask.save()
    results,col,tar_dbname = incept.inception_check(tar_dbname, db_account, sqltext,flag)
    incept.make_sure_mysql_usable()
    status='executed'
    c_time = mytask.create_time
    mytask.update_time = timezone.now()
    backup_status = mytask.backup_status
    if flag == 1:
        backup_status = 2
    for row in results:
        try:
            inclog = Incep_error_log(task_id=task_id,myid=row[0],stage=row[1],errlevel=row[2],stagestatus=row[3],errormessage=row[4],\
                         sqltext=row[5],affectrow=row[6],sequence=row[7],backup_db=row[8],execute_time=row[9],sqlsha=row[10],\
                         create_time=c_time,finish_time=mytask.update_time)
            inclog.save()
            #if some error occured in inception_check stage
        except Exception,e:
            inclog = Incep_error_log(task_id=task_id,myid=999,stage='',errlevel=999,stagestatus='',errormessage=row[0],\
                         sqltext=e,affectrow=999,sequence='',backup_db='',execute_time='',sqlsha='',\
                         create_time=c_time,finish_time=mytask.update_time)
            inclog.save()
        if (int(row[2])!=0):
            status='executed failed'
            backup_status = 1

            #record error message of incept exec
    mytask.backup_status = backup_status
    mytask.status = status
    mytask.save()
    sendmail_task.delay(task_id)

@shared_task()
def parse_binlog(serverid,binname_start,begintime,tbname,dbselected,username,countnum,flash_back,binname_end='',endtime=''):
    flag = True
    pc = prpcrypt()
    insname = Db_instance.objects.get(id=serverid)
    db_account = Db_account.objects.filter(instance=insname, db_account_role='admin')
    if len(db_account) > 0:
        tar_username = db_account[0].user
        tar_passwd = pc.decrypt(db_account[0].passwd)

        connectionSettings = {'host': insname.ip, 'port': int(insname.port), 'user': tar_username, 'passwd': tar_passwd}
        binlogsql = binlog2sql.Binlog2sql(connectionSettings=connectionSettings, startFile=binname_start,
                                          startPos=4, endFile=binname_end, endPos=0,
                                          startTime=begintime, stopTime=endtime, only_schemas=None if dbselected == '0' else dbselected,
                                          only_tables=None if tbname == '0' else tbname, nopk=False, flashback=flash_back, stopnever=False,countnum=countnum)
        binlogsql.process_binlog()
        sqllist = binlogsql.sqllist
        # sendmail_sqlparse.delay(username, dbselected, tbname, sqllist,flash_back)
        sendmail_sqlparse.delay(username, '{}:{}__{}'.format(insname.ip,insname.port,'ALL' if  dbselected == '0' else dbselected), tbname, sqllist, flash_back)
        print sqllist
        return sqllist
    else:
        return ['Instance do not have admin role db account!']


def parse_binlogfirst(insname,binname_start,binname_end,countnum):
    flag = True
    pc = prpcrypt()
    db_account = Db_account.objects.filter(instance=insname,db_account_role='admin')
    if len(db_account) > 0:
        tar_username = db_account[0].user
        tar_passwd = pc.decrypt(db_account[0].passwd)
        connectionSettings = {'host': insname.ip, 'port': int(insname.port), 'user': tar_username, 'passwd': tar_passwd}
        binlogsql = binlog2sql.Binlog2sql(connectionSettings=connectionSettings, startFile=binname_start,
                                          startPos=4, endFile=binname_end if binname_end <> '0' else None , endPos=0,
                                          startTime='', stopTime='', only_schemas='',
                                          only_tables='', nopk=False, flashback=False, stopnever=False,countnum=countnum)
        binlogsql.process_binlog()
        sqllist = binlogsql.sqllist
        return sqllist
    else:
        return ['Instance do not have admin role db account!']




@shared_task()
def sendmail_sqlparse(user,db,tb,sqllist,flashback):
    mailto=[]
    if flashback==True:
        title = "BINLOG PARSE (UNDO) FOR "+ db + "." + ('ALL' if tb == '0' else tb)
    else:
        title = db
    mailto.append(UserInfo.objects.get(username=user).email)
    html_content = loader.render_to_string('include/mail_template.html', locals())
    sendmail(title, mailto, html_content)

@shared_task()
def sendmail_task(task):
    task = Task.objects.get(id=task)
    # tmp=u'x'

    try:
        mailto = []
        for i in User_profile.objects.filter(task_email__gt=0):
            if len(i.user.email) > 0:
                mailto.append(i.user.email)
        # if type(task) != type(tmp):
        if task.status != 'NULL':
            # del tmp
            mailto.append(UserInfo.objects.get(username=task.user).email)
            result_status = Incep_error_log.objects.filter(create_time=task.create_time).filter(finish_time=task.update_time).order_by("myid")
            title = 'Task ID:' + str(task.id) + '  has finished'
        else:
            title = "You have received new task!"
            tmp = task
        html_content = loader.render_to_string('include/mail_template.html', locals())
        sendmail(title, mailto, html_content)

    except Exception ,e:
        print e

@shared_task()
def sendmail_forget(sendto,title,message):
    mailto=[]
    message=message
    mailto.append(sendto)
    html_content = loader.render_to_string('include/mail_template.html', locals())
    sendmail(title, mailto, html_content)


def sendmail (title,mailto,html_content):
    try:
        msg = EmailMultiAlternatives(title, html_content, EMAIL_SENDER, mailto)
        msg.attach_alternative(html_content, "text/html")
        msg.send()
        print(1)
    except Exception,e:
        print e

def task_run(idnum,request):
    while 1:
        try:
            task = Task.objects.get(id=idnum)
        except:
            continue
        break

    if task.status!='executed' and task.status!='running' and task.status!='NULL':
        # db_account = Db_account.objects.get(instance__ip=task.instance.split(':')[0],instance__port=task.instance.split(':')[1],db_account_role=task.db_account_role)
        db_tag = task.instance+'__'+task.db_account_role
        tar_dbname = task.dbtag
        sql = task.sqltext
        mycreatetime = task.create_time
        incept.log_incep_op(sql,tar_dbname,db_tag,request,mycreatetime)
        status='running'
        task.status = status
        task.operator  = request.user.username
        task.update_time = timezone.now()
        task.save()
        # process_runtask(task_id=idnum)
        process_runtask.delay(task_id=idnum)
        return ''
    elif task.status=='NULL':
        return 'PLEASE CHECK THE SQL FIRST'
    else:
        return 'Already executed or in running'



