#Do blooming corrections on a DMSP image following the procedure of Cao et al. 2019
#imports-----------------------------------------------------------------------
import rasterio as rio
import numpy as np
import pandas as pd
import time, os
from matplotlib import pyplot
from scipy import ndimage
import statsmodels.api as sm


#Functions---------------------------------------------------------------------
def open_profile(aoi_f):
    with rio.open(aoi_f) as aoi_o:
        profile = aoi_o.profile
    return profile

def open_rasters(dmsp_f, aoi_rgn):
    with rio.open(dmsp_f) as dmsp_o, rio.open(aoi_rgn) as rgn_o:
        dmsp_a=dmsp_o.read(1)
        rgn_a=rgn_o.read(1)
    return dmsp_a, rgn_a

def select_plps(dmsp_a):
    struct2 = ndimage.generate_binary_structure(2, 2) #kernel that counts all queen neighbours
    plp_set=((dmsp_a!=0) & (ndimage.binary_dilation(dmsp_a==0, structure=struct2)==1))
    print("count PLPs: "+str(np.count_nonzero(plp_set))) #count pixels that are non-zero and have a zero neighbour
    return plp_set

def invdstsum(sa):#Return sum of subarray divided by squared distance to centre if DN>pixel and zero otherwise
    return (np.divide(sa,((np.mgrid[0:7, 0:7][0] - (3, 3)[0])**2 + (np.mgrid[0:7, 0:7][1] - (3, 3)[1])**2),out=np.zeros_like(sa).astype('float64'), where=sa>sa[3][3])).sum()
        
def dist_sum_dn(dmsp_a):
    #loop over pixels with DN>0
    print("count DN>0: "+str(np.count_nonzero(dmsp_a)))
    pnt_x, pnt_y = (dmsp_a).nonzero() #get all coordinates where DN>0 
    mask = (pnt_x > 3) & (pnt_y > 3) & (pnt_x < dmsp_a.shape[0]-4) & (pnt_y < dmsp_a.shape[1]-4) #mask out any points near edge of dataset
    pnt_x, pnt_y = pnt_x[mask],pnt_y[mask]
    R=np.zeros_like(dmsp_a) #empty array to 
    start = time.time()
    for i in range(len(pnt_x)):
        x=pnt_x[i]
        y=pnt_y[i]
        R[x][y]=invdstsum(dmsp_a[x-3:x+4,y-3:y+4])
    end = time.time()
    print(f"Runtime of this loop is {end - start} seconds")   
    return R
    
def stacked_to_df(plp_set,R,dmsp_a, rgn_a):
    stacked=np.stack((plp_set,R,dmsp_a, rgn_a),axis=0) 
    columns=np.transpose(np.reshape(stacked,(4,-1)))
    df=pd.DataFrame(columns, columns=["PLP", "TotR", "DN", "REGID"])
    df=df[df['PLP']==1] 
    return df

def estimate_regional_params(df, rgn_a):
    a_hat=[]
    b_hat=[]
    for r in np.unique(rgn_a): #do for each world region (note that off-coast cells get lumped into their own 'region')
        results=sm.formula.ols(formula = "DN ~ TotR", data=df[df['REGID']==r]).fit()
        results.summary()
        a_hat.append(results.params[1])
        b_hat.append(results.params[0])
    return a_hat,b_hat
        
def apply_params(dmsp_a,rgn_a,R,a_hat,b_hat,aoi_prf):
    blfx_a=dmsp_a
    for i,r in enumerate(np.unique(rgn_a)): #do for each world region (note that off-coast cells get lumped into their own 'region')
        a=a_hat[i]
        b=b_hat[i]
        blfx_a=np.where((dmsp_a!=0) & (rgn_a==r),dmsp_a-b-a*R,blfx_a)
    blfx_a=ndimage.convolve(blfx_a, np.full((3, 3), 1.0/9), mode='constant', cval=0.0) #apply a mean filter on a 3x3 moving window to remove 'unreliable' results 
    blfx_a[blfx_a<0]=0 #replace all negatives with zeros
    blfx_a=np.rint(blfx_a).astype(aoi_prf['dtype']) #round to integer   
    return blfx_a     
        
def save_to_file(blfx_a,blfx_f,aoi_prf):
    with rio.open(blfx_f, 'w', **aoi_prf) as dst:
        dst.write(blfx_a, 1)  
        
        
def main(dmsp_d,aoi_rgn,blfx_f):
    aoi_prf=open_profile(aoi_rgn)
    dmsp_lst=[f for f in os.listdir(dmsp_d) if f.startswith("DMSP")]
    for dmsp_f in dmsp_lst:
        print("Blooming correction for "+dmsp_f[:len("DMSP")+4])
        year=dmsp_f[len("DMSP"):len("DMSP")+4]
        if int(year)<=2013: #double check that the year is right
            #0. Open rasters
            dmsp_a,rgn_a=open_rasters(dmsp_d+dmsp_f, aoi_rgn)
            
            #1. Select PLPs as pixels with weak brightness (DN > 0) but one or more of its eight neighbors are dark (DN=0).
            plp_set=select_plps(dmsp_a)
        
            #2.	Create a distance weighted sum of DN for neighbouring pixels
            #NB: They use a moving window of 7x7 pixels around the PLP, i.e. all pixels within 3.5km. 
            #NB: Further, they drop all pixels with a DN less than that of the PLP.    
            R=dist_sum_dn(dmsp_a)
            
            #3. Create dataset with PLP indicator, Total Neighbour Radiance, DN, and region indicator
            df=stacked_to_df(plp_set,R,dmsp_a, rgn_a)
            
            #3a. Estimate model parameters using PLP sample
            #NB: they estimate a and b with a 150x150km moving window to get 'local' estimates' - we estimate by world regions instead 
            a_hat,b_hat=estimate_regional_params(df, rgn_a)
            
            #3b. Apply model for each bright (DN>0) pixel to remove the blooming effect
            #NB: Also, they apply a mean filter on a 3x3 moving window to remove 'unreliable' results 
            #NB: In addition, I replace all negatives with zeros and round to integer (they don't mention this but seems sensible)
            blfx_a=apply_params(dmsp_a,rgn_a,R,a_hat,b_hat,aoi_prf)
            
            #4. Save to file
            save_to_file(blfx_a,blfx_f.format(y=year),aoi_prf)


#SCRIPT------------------------------------------------------------------------
if __name__ == "__main__":
    # execute only if run as a script
    main(dmsp_d, aoi_rgn,blfx_f)
 
#END---------------------------------------------------------------------------