from PIL import Image
from pathlib import Path
import os 
import threading
import shutil
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'  
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
from util import *
import tensorflow as tf
from tensorflow.keras.models import Model, load_model
from sixdrepnet import SixDRepNet


filt_folder = 'filter_data'
save_folder = 'PexelHQ_Frontal'
save_large_headpose = 'PexelHQ_LargeheadPose'

model = load_model('./facert_model.hdf5',compile=False)
align_model = load_model('./baseline_84pts_220210.hdf5',custom_objects={'custom_loss_fn': custom_loss_fn, 'custom_loss_fn_168': custom_loss_fn},compile=False)
headpose_model = SixDRepNet(gpu_id = -1)


filepaths = list(Path(filt_folder).glob('**/*.jpg'))
for filepath in filepaths:
    basename = filepath.name
    filepath = str(filepath)
  

    print('Loading:',filepath)
   
    filepath = os.path.normpath(filepath)

    if not os.path.isdir(save_folder):
        os.makedirs(save_folder)
    
    try:
        # img = cv.imread(str(filepath))
        img = cv_imread(str(filepath))
        height,width = img.shape[:2]
    except:
        print('loading file error:',str(filepath))
        continue

    pitch, yaw, roll = headpose_model.predict(img)
    if abs(roll ) <= 15 and abs(yaw) <= 15 and abs(pitch) <= 15:
            save_path = os.path.join(save_folder,basename)
    else:
        save_path = os.path.join(save_large_headpose,basename)
        
    is_success = filter_bigface(model,align_model,img,save_path)
    if not is_success:        
        # os.remove(filepath)
        print('No face detect:',filepath)
        # shutil.copy(filepath,"NoFace")
        # os.remove(filepath)


