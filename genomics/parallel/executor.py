# -*- coding: utf-8 -*-
'''
.. module:: genomics.parallel.executor
   :synopsis: Process executor supporting local and grid environments
   :noindex:

.. moduleauthor:: Tiago Antao <tra@popgen.net>


Provides classes to execute parallel processes under several environments.

Some classes are not tested for long and might not be working.

There should be a abstract parent class, but that is not so.
'''

import getpass
import multiprocessing
import os
import subprocess
import time


class Local:
    '''Local executor.

    :param limit: CPU load limit

         if limit is an int>0 then it is the expected load average NOT
         to be used, for instance if there are 32 cores and there
         is a limit of 6, the system will try to never ago above 26.
         A float between 0 and 1 will be interpreted as a the
         fraction of CPUs to be used, e.g., with 32 cores, 0.25
         will use at most 8
         A negative value will be interpreted as the maximum number
         of processes that can be executed in parallel.

    This executor will block if there are no more slots available!
    '''

    def __init__(self, limit):
        self.limit = limit
        self.cpus = multiprocessing.cpu_count()
        self.running = []

    def clean_done(self):
        '''Removes dead processes from the running list.
        '''
        dels = []
        for rIdx, p in enumerate(self.running):
            if p.poll() is not None:
                dels.append(rIdx)
        dels.reverse()
        for del_ in dels:
            del self.running[del_]

    def wait(self, for_all=False):
        '''Blocks if there are no slots available

        :param for_all: Also waits if there is *ANY* job running (i.e.
                    block/barrier)
        '''
        self.clean_done()
        numWaits = 0
        if self.limit > 0 and type(self.limit) == int:
            cond = 'len(self.running) >= self.cpus - self.limit'
        elif self.limit < 0:
            cond = 'len(self.running) >= - self.limit'
        else:
            cond = 'len(self.running) >= self.cpus * self.limit'
        while eval(cond) or (for_all and len(self.running) > 0):
            time.sleep(1)
            self.clean_done()
            numWaits += 1

    def submit(self, command, parameters):
        '''Submits a job
        '''
        self.wait()
        if hasattr(self, 'out'):
            out = self.out
        else:
            out = '/dev/null'
        if hasattr(self, 'err'):
            err = self.err
        else:
            err = '/dev/null'
        if err == 'stderr':
            errSt = ''
        else:
            errSt = '2> ' + err
        p = subprocess.Popen('%s %s > %s %s' %
                             (command, parameters, out, errSt),
                             shell=True)
        self.running.append(p)
        if hasattr(self, 'out'):
            del self.out
        if hasattr(self, 'err'):
            del self.err


class Pseudo:
    '''The pseudo executor.

        This executor will Dump of list of nohup nice commands
    '''
    def __init__(self, outFile="/tmp/pseudo%d" % (os.getpid())):
        '''
           outFile is where the text is written
        '''
        self.outFile = open(outFile, "a")
        pass

    def submit(self, command, parameters):
        '''Submits a job
        '''
        self.outFile.write('nohup /usr/bin/nice -n19 %s %s > %s\n' %
                           (command, parameters, self.out))
        self.outFile.flush()

    def __del__(self):
        self.outFile.close()


class LSF:
    '''The LSF executor.

    .. danger:: This is not tested for long. Probably does not work

    '''
    def __init__(self):
        '''Constructor

        '''
        self.running = []
        self.queue = 'normal'  # Default queue name is "normal"
        self.mem = 4000  # Request 4GB as a default
        self.numPasses = 0
        self.outDir = os.path.expanduser("~/tmp")
        self.cnt = 1

    def clean_done(self):
        '''Removes dead processes from the running list.
        '''
        ongoing = []
        statusFile = '/tmp/farm-%d' % (os.getpid())
        os.system('bjobs > %s 2>/dev/null' % statusFile)
        f = open(statusFile)
        f.readline()  # header
        for l in f:
            toks = list(filter(lambda x: x != '', l.rstrip().split(' ')))
            ongoing.append(int(toks[0]))
        os.remove(statusFile)
        myDels = []
        for rIdx, p in enumerate(self.running):
            if p not in ongoing:
                myDels.append(rIdx)
        myDels.reverse()
        for myDel in myDels:
            del self.running[myDel]

    def wait(self, for_all=False, be_careful=60):
        '''Blocks according to some condition

           :param for_all: Also waits if there is *ANY* job running (i.e.
                    block/barrier)

           :param be_careful: Wait X secs before starting. This is because
                       tasks take time to go into the pool.
        '''
        time.sleep(be_careful)
        if for_all:
            while len(self.running) > 0:
                self.clean_done()
                time.sleep(1)

    def submit(self, command, parameters='', myDir=os.getcwd()):
        '''Submits a job
        '''
        M = self.mem * 1000
        job = "bsub -G malaria-dk -P malaria-dk -q %s "
        job += "-o quickrun.%s.out -e quickrun.%s.err "
        job += "-J quickrun.%s -M %d -R "
        job += "'select[type==X86_64 && mem>%d] "
        job += "rusage[mem=%d]' \"cd %s ; %s %s \""
        job = job % (self.queue, self.cnt, self.cnt, self.cnt, M,
                     self.mem, self.mem, myDir, command, parameters)

        statusFile = "/tmp/farm-%d.%d" % (os.getpid(), self.cnt)
        os.system(job + " >%s 2>/dev/null" % statusFile)
        f = open(statusFile)
        l = f.readline()
        job = int(l[l.find("<") + 1:l.find(">")])
        f.close()
        os.remove(statusFile)
        self.cnt += 1

        self.running.append(job)
        self.numPasses += 1


class SGE:
    ''' The SGE executor.

    '''
    def __init__(self, mailUser=None):
        '''Constructor

        '''
        self.running = []
        self.queue = "normal"  # Default queue name is "normal"
        self.mem = 1000  # Request 1GB as a default
        self.outDir = os.path.expanduser("~/tmp")
        self.cnt = 1
        self.project = "anopheles"
        self.outDir = "/tmp"
        self.mailOptions = "a"
        self.mailUser = mailUser
        self.maxProc = 1000
        self.hosts = []
        self.cpus = 1

    def clean_done(self):
        '''Removes dead processes from the running list.
        '''
        ongoing = []
        statusFile = "/tmp/farm-%d" % (os.getpid())
        os.system("qstat > %s 2>/dev/null" % statusFile)
        f = open(statusFile)
        f.readline()  # header
        f.readline()  # header
        for l in f:
            toks = list(filter(lambda x: x != "", l.rstrip().split(" ")))
            ongoing.append(int(toks[0]))
        os.remove(statusFile)
        myDels = []
        for rIdx, p in enumerate(self.running):
            if p not in ongoing:
                myDels.append(rIdx)
        myDels.reverse()
        for myDel in myDels:
            del self.running[myDel]

    def wait(self, forAll=False, beCareful=60):
        '''Blocks according to some condition

           forAll - Also waits if there is *ANY* job running (i.e.
                    block/barrier)

           beCareful - Wait X secs before starting. This is because
                       tasks take time to go into the pool.
        '''
        time.sleep(beCareful)
        self.clean_done()
        if forAll:
            while len(self.running) > 0:
                self.clean_done()
                time.sleep(1)

    def submit(self, command, parameters="", myDir=os.getcwd()):
        '''Submits a job
        '''
        jobFile = "/tmp/job-%d.%d" % (os.getpid(), self.cnt)
        w = open(jobFile, "w")
        w.write("%s %s\n" % (command, parameters))
        w.close()

        if self.mailUser is not None:
            mail = "-m %s -M %s" % (self.mailOptions, self.mailUser)
        else:
            mail = ""
        while len(self.running) > self.maxProc:
            self.wait(beCareful=5)
        hosts = ""
        if len(self.hosts) > 0:
            hosts = " -q "
        for host in self.hosts:
            hosts += "\*@%s" % host
            if host != self.hosts[-1]:
                hosts += ","
        job = "qsub %s %s -S /bin/bash -V -P %s -cwd -l h_vmem=%dm %s " % (
            mail, hosts, self.project, self.mem, jobFile)
        statusFile = "/tmp/farm-%d.%d" % (os.getpid(), self.cnt)
        os.system(job + " >%s 2>/dev/null" % statusFile)
        f = open(statusFile)
        l = f.readline()
        job = int(l.split(" ")[2])
        f.close()
        os.remove(statusFile)
        os.remove(jobFile)
        self.cnt += 1

        self.running.append(job)


class Torque:
    ''' The Torque executor.

    '''
    def __init__(self, mailUser=None):
        '''Constructor

        '''
        self.running = []
        self.cnt = 0
        self.mem = 1000  # mb
        self.cpus = 1
        self.out = None
        self.queue = "long"  # hard-coded default...

    def clean_done(self):
        '''Removes dead processes from the running list.
        '''
        ongoing = []
        statusFile = "/tmp/farm-%d" % (os.getpid())
        os.system("qstat > %s 2>/dev/null" % statusFile)
        f = open(statusFile)
        f.readline()  # header
        f.readline()  # header
        me = getpass.getuser()
        for l in f:
            toks = list(filter(lambda x: x != "", l.rstrip().split(" ")))
            user = toks[2]
            if user != me:
                continue
            pid = toks[0]
            ongoing.append(int(pid.split(".")[0]))
        #os.remove(statusFile)
        myDels = []
        for rIdx, p in enumerate(self.running):
            if p not in ongoing:
                myDels.append(rIdx)
        myDels.reverse()
        for myDel in myDels:
            del self.running[myDel]

    def wait(self, forAll=False, beCareful=60):
        '''Blocks according to some condition

           forAll - Also waits if there is *ANY* job running (i.e.
                    block/barrier)

           beCareful - Wait X secs before starting. This is because
                       tasks take time to go into the pool.
        '''
        time.sleep(beCareful)
        self.clean_done()
        if forAll:
            while len(self.running) > 0:
                self.clean_done()
                time.sleep(1)

    def submit(self, command, parameters="", myDir=os.getcwd()):
        '''Submits a job
        '''
        jobFile = "/tmp/job-%d.%d" % (os.getpid(), self.cnt)
        w = open(jobFile, "w")
        w.write("#PBS -l mem=%dmb,vmem=%dmb\n" % (self.mem, self.mem))
        w.write("#PBS -q %s\n" % self.queue)
        if self.out is not None:
            w.write("#PBS -o %s\n" % self.out)
            self.out = None
        w.write("cd %s\n" % os.getcwd())
        w.write("%s %s\n" % (command, parameters))
        w.close()

        job = "qsub %s" % (jobFile,)
        statusFile = "/tmp/farm-%d.%d" % (os.getpid(), self.cnt)
        os.system(job + " >%s 2>/dev/null" % statusFile)
        f = open(statusFile)
        l = f.readline()
        job = int(l.split(".")[0])
        f.close()
        os.remove(statusFile)
        os.remove(jobFile)
        self.cnt += 1

        self.running.append(job)


class SLURM:
    ''' The SLURM executor.

    '''
    def __init__(self, mailUser=None):
        '''Constructor

        '''
        self.running = []
        self.cnt = 0
        self.mem = 1000  # mb
        self.cpus = 1
        self.out = None
        self.partition = "main"  # hard-coded default...

    def clean_done(self):
        '''Removes dead processes from the running list.
        '''
        ongoing = []
        statusFile = "/tmp/farm-%d" % (os.getpid())
        os.system("squeue > %s 2>/dev/null" % statusFile)
        f = open(statusFile)
        f.readline()  # header
        me = getpass.getuser()
        for l in f:
            toks = list(filter(lambda x: x != "", l.rstrip().split(" ")))
            user = toks[3]
            if user != me:
                continue
            pid = toks[0]
            ongoing.append(int(pid))
        #os.remove(statusFile)
        myDels = []
        for rIdx, p in enumerate(self.running):
            if p not in ongoing:
                myDels.append(rIdx)
        myDels.reverse()
        for myDel in myDels:
            del self.running[myDel]

    def wait(self, forAll=False, beCareful=60):
        '''Blocks according to some condition

           forAll - Also waits if there is *ANY* job running (i.e.
                    block/barrier)

           beCareful - Wait X secs before starting. This is because
                       tasks take time to go into the pool.
        '''
        time.sleep(beCareful)
        self.clean_done()
        if forAll:
            while len(self.running) > 0:
                self.clean_done()
                time.sleep(1)

    def submit(self, command, parameters='', myDir=os.getcwd()):
        '''Submits a job
        '''
        jobFile = "/tmp/job-%d.%d" % (os.getpid(), self.cnt)
        w = open(jobFile, "w")
        if self.out is not None:
            out = '-o %s' % self.out
            self.out = None
        else:
            out = ''
        w.write('#!/bin/bash\n')
        w.write("%s %s\n" % (command, parameters))
        w.close()

        statusFile = "/tmp/farm-%d.%d" % (os.getpid(), self.cnt)
        job = "sbatch --mem=%d %s -p %s %s" % (self.mem, out,
                                               self.partition, jobFile)
        statusFile = "/tmp/farm-%d.%d" % (os.getpid(), self.cnt)
        os.system(job + " > %s 2> /dev/null" % statusFile)
        f = open(statusFile)
        l = f.readline()
        job = int(l.rstrip().split(" ")[-1])
        f.close()
        os.remove(statusFile)
        os.remove(jobFile)
        self.cnt += 1

        self.running.append(job)
