#!/usr/bin/env python

import json
import urllib2
import urllib
import subprocess
from subprocess import call
import os
import time
import re


backend="http://10.16.0.7"


# We no longer hardcode muxer_id and take it from the kernel commandline instead
# might seem extreme, but means we can trivially PXE boot muxer instances
#muxer_id=1

host_header="videobackend"
hls_enc_path="/home/pi/HLS-Stream-Creator/HLS-Stream-Creator.sh"
mount_point='/home/pi/remote/'
base_dir=mount_point + 'videos/'
seg_length=5

logfile="/home/pi/mux.log"
lockfile="/home/pi/hlsmux.lock"

FFMPEG_INPUT_FLAGS=''
FFMPEG_FLAGS='-c:v h264_omx -preset fast -hide_banner -strict -2 -loglevel quiet'
NUMTHREADS=2

output_rates = [0.5]

TEMPFILE="/home/pi/tmpdir/tmp.mp4"

def writestat(statstr):
    print(statstr)
    f = open(logfile,'a+')
    f.write("{}: {}\n".format(time.time(),statstr))
    f.close()
    
def check_is_mounted(dir_path):
    ''' Check a given path is a mountpoint.
    If not, attempt to mount it
    '''
    
    if not os.path.ismount(dir_path):
        try:
            subprocess.check_call(["mount", dir_path])
        except:
            # Failed
            return False
        
    return True

def sorted_nicely( l ):
    """ Sorts the given iterable in the way that is expected.
 
    Required arguments:
    l -- The iterable to be sorted.
 
    """
    convert = lambda text: int(text) if text.isdigit() else text
    alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(l, key = alphanum_key)


def probe_file(filename):

    cmnd = ['ffprobe', '-show_streams', '-print_format', 'json', '-loglevel', 'quiet', filename]
    p = subprocess.Popen(cmnd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err =  p.communicate()
    if err:
        writestat("========= error ========")
        writestat(err)
        return False
    j = json.loads(out)
    return j


def calcBitrates(j, output_rates):

    bitrates = []
    for s in j['streams']:
        if s['codec_type'] != 'video':
            continue
        elif "bit_rate" not in s:
            continue

        br = int(s['bit_rate'])

        # Work out the bitrates to generate
        bitrates.append(str(int(br/1000)))

        # Don't bother with other bitrates if it's already < 250K
        if int(s['bit_rate']) > 250:

            for mod in output_rates:
                # Convert to kb/s
                # We caste to an int to remove any decimal places, and then to a string so we dont break join()
                bitrates.append(str(int((br * mod)/1000)))


            if int(bitrates[-1]) > 250:
                # Make sure there's a 200kb/s option
                bitrates.append(str(200))

        return sorted_nicely(bitrates)
    
    return False


def getNextJob():
    
    if os.path.exists(lockfile):
        writestat("LOCKED: Out of service file {} detected. Refusing to fetch jobs".format(lockfile))
        return False
    
    method = "POST"
    handler = urllib2.HTTPHandler()

    url="%s/muxer/%s" % (backend,muxer_id)

    opener = urllib2.build_opener(handler)
    request = urllib2.Request(url)
    request.add_header("Host",host_header)
    request.get_method = lambda: method

    try:
        connection = opener.open(request)
    except urllib2.HTTPError,e:
        connection = e

    # check. Substitute with appropriate HTTP code.
    if connection.code == 200:
        data = connection.read()
        job = json.loads(data)
        if job['status'] == 'empty':
            # Currently no jobs
            return False

        # Otherwise trigger the job 
        triggerMux(job)
        return True
        
        
def triggerMux(job):
    
    # Call the job
    
    if not check_is_mounted(mount_point):
        writestat("ERROR: Output dir not mounted, and failed to mount it")
        writestat("Will try again in 1 minute")
        time.sleep(60)
        return False
    
    
    
    notify_change('inprocess',job)
    writestat("Got a job ID: {}".format(job['job']['id']))
    path = "%s%s" % (base_dir,job['job']['path'])
    
    pathsplit = job['job']['path'].split("/")
    
    vidname=pathsplit[-1]
    viddir="%s.hls" % (path)
    
    if os.path.isdir(viddir):
        # Don't try and redo work that's already done
        writestat("already seem to have %s" % (viddir,))
        notify_change('failed',job,{'reason':'DirectoryExists'})
        return False
    
    if not os.path.isfile(path):
        # Do nothing if the source file is missing
        writestat("File doesn't exist %s" % (path,))
        notify_change('failed',job,{'reason':'MissingFile'})
        return False
        
    
    os.mkdir(viddir)
    
    json = probe_file(path)
    brs = calcBitrates(json, output_rates)
    
    if not brs:
        writestat("ERROR: Couldn't ascertain source bitrate")
        notify_change('failed',job,{'reason':'CantGetBitRate'})
        return False        
    
    bitrates = ','.join(brs)
    
    my_env = os.environ.copy()
    my_env["FFMPEG_INPUT_FLAGS"] = FFMPEG_INPUT_FLAGS
    my_env["FFMPEG_FLAGS"] = FFMPEG_FLAGS
    my_env["NUMTHREADS"] = str(NUMTHREADS)
    
    
    cmd = ['ffmpeg','-y','-i',str(path)]
    
    for arg in FFMPEG_FLAGS.split(' '):
        cmd.append(arg)
    
    cmd.append(TEMPFILE)
    
    # Make a copy of the file to strip any unsupported tracks (OS-22)
    writestat("Copying {} and stripping unneeded tracks".format(path))
    proc = subprocess.Popen(cmd,env=my_env)    
    proc.wait()
    
    start = int(time.time())
    writestat("Triggering Mux")
    proc = subprocess.Popen([hls_enc_path,'-i',TEMPFILE,'-o',str(viddir),'-b',str(bitrates),'-p','manifest','-t','media','-s',str(seg_length)],env=my_env)
    
    proc.wait()
    
    os.remove(TEMPFILE)
    
    if (int(time.time()) - start) < 5:
        # Completed too quick, ffmpeg errored
        writestat("FFMpeg exited %s" % (path,))
        notify_change('failed',job,{'reason':'FFmpegErr'})
        return False

    notify_change('complete',job)




def notify_change(state,job,data=False):
    method = "POST"
    handler = urllib2.HTTPHandler()
    url="%s/muxer/%s/%s/%s" % (backend,state,muxer_id,job['job']['id'])

    opener = urllib2.build_opener(handler)
    
    if data:
        data = urllib.urlencode(data)
        request = urllib2.Request(url,data)
    else:
        request = urllib2.Request(url)
    
    
    request.add_header("Host",host_header)
    request.get_method = lambda: method

    try:
        connection = opener.open(request)
    except urllib2.HTTPError,e:
        connection = e

    # check. Substitute with appropriate HTTP code.
    if connection.code == 200:
        data = connection.read()

        


def getNextTidy():
    if os.path.exists(lockfile):
        writestat("LOCKED: Out of service file {} detected. Refusing to fetch jobs".format(lockfile))
        return False
    
    method = "POST"
    handler = urllib2.HTTPHandler()

    url="%s/tidy/%s" % (backend,muxer_id)

    opener = urllib2.build_opener(handler)
    request = urllib2.Request(url)
    request.add_header("Host",host_header)
    request.get_method = lambda: method

    try:
        connection = opener.open(request)
    except urllib2.HTTPError,e:
        connection = e

    # check. Substitute with appropriate HTTP code.
    if connection.code == 200:
        data = connection.read()
        jobs = json.loads(data)
    
        if jobs['status'] == 'empty':
            # Currently no jobs
            return False

        for job in jobs['files']:
            # Otherwise trigger the job 
            tidyfile(job)



def tidyfile(job):
    orig = "%s/%s" % (base_dir,job['path'])
    dest = "%s/%s" % (base_dir.replace('videos','originals'),job['path'])
    
    #print dest
    
    destcont = '/'.join(dest.split("/")[0:-1])
    
    if not os.path.isdir(destcont):
        os.makedirs(destcont)
    
    writestat("Tidying {}".format(job['path']))
    
    try:
        os.rename(orig,dest)
    except:
        writestat("File not there!")
    
    notifyTidied(job)
    

def notifyTidied(job):
    method = "POST"
    handler = urllib2.HTTPHandler()

    url="%s/tidy/complete/%s/%s" % (backend,muxer_id,job['id'])
    writestat("Sending notify to {}".format(url))

    opener = urllib2.build_opener(handler)
    request = urllib2.Request(url)
    request.add_header("Host",host_header)
    request.get_method = lambda: method

    try:
        connection = opener.open(request)
    except urllib2.HTTPError,e:
        connection = e

    # check. Substitute with appropriate HTTP code.
    if connection.code == 200:
        data = connection.read()
    




f = open("/proc/cmdline", "r")
cmdline = f.read().split(' ')
f.close()

muxer_id = False
for arg in cmdline:
    if arg[0:6] == "muxid=":
        muxer_id=arg[6:].replace("\n","")
        writestat("Got muxer id {} from boot config".format(muxer_id))

    if arg == 'muxhwdec=1':
        FFMPEG_INPUT_FLAGS='-c:v h264_mmal'
        
    
if not muxer_id:
    muxer_id=1
    writestat("Using default muxer id")


while True:
    # Loop indefinitely
    if not getNextJob():
        # No jobs at the moment
        time.sleep(60)

    # We also need to tidy files based on the servers schedule
    getNextTidy()


