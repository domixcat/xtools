# -*- coding=utf-8
import sys
import os
import logging
import optparse
import tarfile
import shutil
import requests
import datetime

# cos
from qcloud_cos import CosConfig
from qcloud_cos import CosS3Client
from qcloud_cos.cos_threadpool import SimpleThreadPool

# cdn
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.cdn.v20180606 import cdn_client, models

# 正常情况日志级别使用INFO，需要定位时可以修改为DEBUG，此时SDK会打印和服务端的通信信息
logging.basicConfig(level=logging.ERROR, stream=sys.stdout)

# 设置用户属性, 包括 secret_id, secret_key, region等。Appid 已在CosConfig中移除，请在参数 Bucket 中带上 Appid。Bucket 由 BucketName-Appid 组成
secret_id = 'SecretId'      # 替换为用户的 SecretId，请登录访问管理控制台进行查看和管理，https://console.cloud.tencent.com/cam/capi
secret_key = 'SecretKey'         # 替换为用户的 SecretKey，请登录访问管理控制台进行查看和管理，https://console.cloud.tencent.com/cam/capi
region = 'ap-guangzhou'      # 替换为用户的 region，已创建桶归属的region可以在控制台查看，https://console.cloud.tencent.com/cos5/bucket
                           # COS支持的所有region列表参见https://cloud.tencent.com/document/product/436/6224
token = None               # 如果使用永久密钥不需要填入token，如果使用临时密钥需要填入，临时密钥生成和使用指引参见https://cloud.tencent.com/document/product/436/14048
cdnPath = "https://xxxx.file.myqcloud.com/"
feishuUrl = 'https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx'

config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key, Token=token)  # 获取配置对象
cosClient = CosS3Client(config)

# 解压文件
def extractTarFile(fname, dirs="."):
    if not os.path.exists(fname):
        return ""

    bname = os.path.basename(fname)
    idx = bname.find(".tar.gz")
    if idx != -1:
        bname = bname[:idx]

    outDir = dirs + os.sep + bname
    if os.path.exists(outDir):
        shutil.rmtree(outDir)
    t = tarfile.open(fname)
    t.extractall(path = outDir)
    return outDir

# 多线程上传目录
def upload(bucket, uploadDir):
    apkPath = ""
    changeLog = ""
    version = ""
    g = os.walk(uploadDir)

    # 创建上传的线程池
    print("Start upload files")
    pool = SimpleThreadPool()
    for path, dir_list, file_list in g:
        for file_name in file_list:
            srcKey = os.path.join(path, file_name)

            cosObjectKey = ""
            idx = srcKey.find(uploadDir)
            if idx != -1:
                cosObjectKey = srcKey[idx+len(uploadDir):]
                cosObjectKey = cosObjectKey.replace("\\", "/")
                cosObjectKey = cosObjectKey.strip('/')

                # 判断是否是 apk 文件
                if os.path.splitext(file_name)[-1] == ".apk":
                    apkPath = cosObjectKey

                # changelog 文件
                if file_name.lower() == "changelog.txt":
                    changeLog = open(srcKey,'r',encoding='UTF-8').read()

                # version
                if file_name.lower() == "version.txt":
                    version = open(srcKey,'r',encoding='UTF-8').read()

                print("upload %s --> %s" % (srcKey, cosObjectKey))
                pool.add_task(cosClient.upload_file, bucket, cosObjectKey, srcKey)

    # 等待线程上传线程结束
    pool.wait_completion()
    result = pool.get_result()
    ok = result['success_all']
    if not ok:
        print("Not all files upload sucessed. you should retry")
    else:
        print("All files upload sucessed.\n")
    return ok, apkPath, changeLog, version

def refreshCDN():
    try:
        cred = credential.Credential(secret_id, secret_key)
        cdnClient = cdn_client.CdnClient(cred, "")

        req = models.PurgePathCacheRequest()
        req.Paths = [cdnPath]
        req.FlushType = "flush"
        resp = cdnClient.PurgePathCache(req)

        #print(resp.to_json_string())
        print("CDN refresh sucessed")
    except TencentCloudSDKException as err:
        print("Refresh cdn fail", err)

def notifyFeiShu(apkKey, changeLog, version):
    contests = [
        "发布类型：" + ("全包" if len(apkKey)>0 else "补丁包"),
        "发布时间：" + datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ]
    if len(version) > 0:
        contests.append("版本号：" + version)
    if len(changeLog) > 0:
        contests.append("\n更新内容：")
        contests.append(changeLog)
    if len(apkKey) > 0:
        cdnURL = cdnPath + apkKey
        contests.append("\n下载地址：")
        contests.append(cdnURL)
        contests.append("\n二维码地址：")
        contests.append("https://api.qrserver.com/v1/create-qr-code/?size=250x250&data="+cdnURL)

    # 发送飞书
    contestsStr = "\n".join(contests)
    headers = { "Content-Type": "application/json"}
    req_body = {
        "content": {"text": contestsStr},
        "msg_type": "text",
    }
    r = requests.post(url=feishuUrl, headers=headers, json=req_body)
    #print(r)
    #print(r.text)
    #print(r.content)

def main():
    usage = "Usage: python3 %prog [options]\n\t e.g: python3 %prog --bucket=examplebucket-1250000000 --tarball=xxx.tar.gz"
    parser = optparse.OptionParser(usage=usage)
    parser.add_option("--bucket", dest="bucket", help="[required] bucket name")
    parser.add_option("--tarball", dest="tarball", help="[required] upload tarball")
    parser.add_option("--exdir", dest="exdir", default=".", help="extract files into exdir")

    options, args = parser.parse_args()
    requireds = ["bucket", "tarball"]
    for opt in requireds:
        if options.__dict__.get(opt) is None:
            parser.print_help()
            return

    uploadDir = extractTarFile(options.tarball, options.exdir)
    if len(uploadDir) == 0:
        print("tarball not exist")
        return

    ok, apkPath, changeLog, version = upload(options.bucket, uploadDir)
    if ok:
        refreshCDN()
        notifyFeiShu(apkPath, changeLog, version)

if __name__ == "__main__":
    #notifyFeiShu("test","0.1","xxx")
    main()