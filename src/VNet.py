import caffe
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
import os
import DataManager as DM
import utilities
from os.path import splitext
from multiprocessing import Process, Queue
import datetime
import logging

DEBUG = False

class VNet(object):
    params = None
    dataManagerTrain = None
    dataManagerTest = None

    def __init__(self,params):
        self.params = params
        # caffe.set_device(self.params['ModelParams']['device'])
        # caffe.set_mode_gpu()

    def prepareDataThread(self, dataQueue, numpyImages, numpyGT):
        nr_iter = self.params['ModelParams']['numIterations']
        batchsize = self.params['ModelParams']['batchsize']

        keysIMG = numpyImages.keys()

        nr_iter_dataAug = nr_iter * batchsize
        np.random.seed()
        whichDataList = np.random.randint(len(keysIMG), size = int(nr_iter_dataAug / self.params['ModelParams']['nProc']))
        whichDataForMatchingList = np.random.randint(len(keysIMG), size = int(nr_iter_dataAug / self.params['ModelParams']['nProc']))

        for whichData, whichDataForMatching in zip(whichDataList, whichDataForMatchingList):
            filename, ext = splitext(keysIMG[whichData])

            currGtKey = filename + '_segmentation' + ext
            currImgKey = filename + ext

            # data agugumentation through hist matching across different examples
            ImgKeyMatching = keysIMG[whichDataForMatching]

            defImg = numpyImages[currImgKey]
            defLab = numpyGT[currGtKey]

            defImg = utilities.hist_match(defImg, numpyImages[ImgKeyMatching])

            if (np.random.rand(1)[0] > 0.5):
                # do not apply deformations always, just sometimes
                defImg, defLab = utilities.produceRandomlyDeformedImage(defImg, defLab,
                                                                        self.params['ModelParams']['numcontrolpoints'],
                                                                        self.params['ModelParams']['sigma'])

            weightData = np.zeros_like(defLab, dtype = float)
            weightData[defLab == 1] = np.prod(defLab.shape) / np.sum((defLab == 1).astype(dtype = np.float32))
            weightData[defLab == 0] = np.prod(defLab.shape) / np.sum((defLab == 0).astype(dtype = np.float32))

            dataQueue.put(tuple((defImg, defLab, weightData)))


    def trainThread(self, dataQueue, solver):
        nr_iter = self.params['ModelParams']['numIterations']
        batchsize = self.params['ModelParams']['batchsize']

        # here we use (D, H, W) layout
        batchData = np.zeros((batchsize, 1, self.params['DataManagerParams']['VolSize'][2], self.params['DataManagerParams']['VolSize'][0], self.params['DataManagerParams']['VolSize'][1]), dtype = float)
        batchLabel = np.zeros((batchsize, 1, self.params['DataManagerParams']['VolSize'][2], self.params['DataManagerParams']['VolSize'][0], self.params['DataManagerParams']['VolSize'][1]), dtype = float)

        # only used if you do weighted multinomial logistic regression
        # batchWeight = np.zeros((batchsize, 1, self.params['DataManagerParams']['VolSize'][2],
        #                        self.params['DataManagerParams']['VolSize'][0],
        #                        self.params['DataManagerParams']['VolSize'][1]), dtype = float)

        train_loss = np.zeros(nr_iter)
        for it in range(nr_iter):
            for i in range(batchsize):
                [defImg, defLab, defWeight] = dataQueue.get()

                batchData[i, 0, :, :, :] = np.transpose(defImg.astype(dtype = np.float32, copy = False), [2, 0, 1])
                batchLabel[i, 0, :, :, :] = np.transpose((defLab > 0.5).astype(dtype = np.float32, copy = False), [2, 0, 1])
                # batchWeight[i, 0, :, :, :] = np.transpose(defWeight.astype(dtype = np.float32, copy = False), [2, 0, 1])

            solver.net.blobs['data'].data[...] = batchData.astype(dtype = np.float32, copy = False)
            solver.net.blobs['label'].data[...] = batchLabel.astype(dtype = np.float32, copy = False)
            # solver.net.blobs['labelWeight'].data[...] = batchWeight.astype(dtype = np.float32)
            # use only if you do softmax with loss

            print "iter: ", it
            solver.step(1)
            train_loss[it] = solver.net.blobs['loss'].data

            if DEBUG:
                if (np.mod(it, 10) == 0):
                    plt.clf()
                    plt.plot(range(0, it), train_loss[0 : it])
                    plt.pause(0.00000001)

                matplotlib.pyplot.show()


    def train(self):
        print "training data: ", self.params['ModelParams']['dirTrain']

        # we define here a data manage object
        self.dataManagerTrain = DM.DataManager(self.params['ModelParams']['dirTrain'],
                                               self.params['ModelParams']['dirResult'],
                                               self.params['DataManagerParams'])

        self.dataManagerTrain.loadTrainingData()

        howManyImages = len(self.dataManagerTrain.sitkImages)
        howManyGT = len(self.dataManagerTrain.sitkGT)

        assert howManyGT == howManyImages

        print "The dataset info: images[" + str(howManyImages) + "], labels[" + str(howManyGT) + "]"

        test_interval = 20
        # Write a temporary solver text file because pycaffe is stupid
        with open("solver.prototxt", 'w') as f:
            f.write("net: \"" + self.params['ModelParams']['prototxtTrain'] + "\" \n")
            f.write("base_lr: " + str(self.params['ModelParams']['baseLR']) + " \n")
            f.write("momentum: 0.99 \n")
            f.write("weight_decay: 0.0005 \n")
            f.write("lr_policy: \"step\" \n")
            f.write("stepsize: 20000 \n")
            f.write("gamma: 0.1 \n")
            f.write("display: 1 \n")
            f.write("snapshot: 500 \n")
            f.write("snapshot_prefix: \"" + self.params['ModelParams']['dirSnapshots'] + "\" \n")
            # f.write("test_iter: 20 \n")
            # f.write("test_interval: " + str(test_interval) + "\n")

        f.close()
        solver = caffe.SGDSolver("solver.prototxt")
        os.remove("solver.prototxt")

        if (self.params['ModelParams']['snapshot'] > 0):
            solver.restore(self.params['ModelParams']['dirSnapshots'] + "_iter_" + str(
                self.params['ModelParams']['snapshot']) + ".solverstate")

        if DEBUG:
            plt.ion()

        numpyImages = self.dataManagerTrain.getNumpyImages()
        numpyGT = self.dataManagerTrain.getNumpyGT()

        # numpyImages['Case00.mhd']
        # numpy images is a dictionary that you index in this way (with filenames)

        for key in numpyImages:
            mean = np.mean(numpyImages[key][numpyImages[key] > 0])
            std = np.std(numpyImages[key][numpyImages[key] > 0])

            numpyImages[key] -= mean
            numpyImages[key] /= std

        dataQueue = Queue(30)
        dataPreparation = [None] * self.params['ModelParams']['nProc']

        for proc in range(0, self.params['ModelParams']['nProc']):
            dataPreparation[proc] = Process(target = self.prepareDataThread, args = (dataQueue, numpyImages, numpyGT))
            dataPreparation[proc].daemon = True
            dataPreparation[proc].start()

        self.trainThread(dataQueue, solver)


    def test(self):
        self.dataManagerTest = DM.DataManager(self.params['ModelParams']['dirTest'], self.params['ModelParams']['dirResult'], self.params['DataManagerParams'])
        self.dataManagerTest.loadTestData()

        net = caffe.Net(self.params['ModelParams']['prototxtTest'],
                        os.path.join(self.params['ModelParams']['dirSnapshots'], "_iter_" + str(self.params['ModelParams']['snapshot']) + ".caffemodel"),
                        caffe.TEST)

        numpyImages = self.dataManagerTest.getNumpyImages()

        for key in numpyImages:
            mean = np.mean(numpyImages[key][numpyImages[key] > 0])
            std = np.std(numpyImages[key][numpyImages[key] > 0])

            numpyImages[key] -= mean
            numpyImages[key] /= std

        results = dict()

        for key in numpyImages:
            btch = np.reshape(numpyImages[key], [1, 1, numpyImages[key].shape[0], numpyImages[key].shape[1], numpyImages[key].shape[2]])

            net.blobs['data'].data[...] = btch

            out = net.forward()
            l = out["labelmap"]
            labelmap = np.squeeze(l[0, 1, :, :, :])

            results[key] = np.squeeze(labelmap)

            self.dataManagerTest.writeResultsFromNumpyLabel(np.squeeze(labelmap), key)

