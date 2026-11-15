package it.freellmapi.remote;

import android.content.Context;
import android.graphics.Rect;
import android.os.Bundle;
import android.text.Editable;
import android.text.Layout;
import android.view.View;
import android.widget.ScrollView;
import android.widget.TextView;

/** Owns the viewport while selectable text, layout and keyboard sizes change. */
public final class TranscriptScrollView extends ScrollView {
    final TextView text;
    boolean following=true, adjusting=false, changing=false;
    int anchor=0, offset=0;
    Runnable followChanged;

    public TranscriptScrollView(Context context,TextView text){
        super(context);this.text=text;
        setSaveEnabled(false);text.setSaveEnabled(false);
        setSmoothScrollingEnabled(false);
        text.setText("",TextView.BufferType.EDITABLE);
        addView(text);
    }
    boolean nearBottom(){return !canScrollVertically(1)||text.getBottom()-getScrollY()-getHeight()<=48*getResources().getDisplayMetrics().density;}
    boolean followsTail(){return following;}
    void remember(){
        Layout layout=text.getLayout();if(layout==null)return;
        int y=Math.max(0,getScrollY()-text.getTop()-text.getTotalPaddingTop());
        int line=layout.getLineForVertical(y);
        anchor=layout.getLineStart(line);offset=y-layout.getLineTop(line);
    }
    void notifyFollow(){if(followChanged!=null)followChanged.run();}
    @Override protected void onScrollChanged(int x,int y,int oldX,int oldY){
        super.onScrollChanged(x,y,oldX,oldY);
        if(!adjusting&&!changing){following=nearBottom();remember();notifyFollow();}
    }
    @Override protected void onSizeChanged(int w,int h,int oldW,int oldH){
        boolean previous=adjusting;adjusting=true;
        try{super.onSizeChanged(w,h,oldW,oldH);}finally{adjusting=previous;}
    }
    @Override protected void onLayout(boolean changed,int left,int top,int right,int bottom){
        adjusting=true;
        try{super.onLayout(changed,left,top,right,bottom);restoreViewport();}
        finally{adjusting=false;}
    }
    void restoreViewport(){
        Layout layout=text.getLayout();if(layout==null)return;
        int y;
        if(following)y=Math.max(0,text.getBottom()-getHeight()+getPaddingBottom());
        else{int line=layout.getLineForOffset(Math.min(anchor,text.length()));y=text.getTop()+text.getTotalPaddingTop()+layout.getLineTop(line)+offset;}
        super.scrollTo(0,y);remember();
    }
    @Override public void requestChildFocus(View child,View focused){
        // TextView's selectable editor must not bring its first line into view.
        boolean previous=adjusting;adjusting=true;int y=getScrollY();
        try{super.requestChildFocus(child,focused);super.scrollTo(0,y);}finally{adjusting=previous;}
    }
    @Override public boolean requestChildRectangleOnScreen(View child,Rect rect,boolean immediate){
        if(child==text)return false;
        return super.requestChildRectangleOnScreen(child,rect,immediate);
    }
    void appendText(String value){
        if(value.isEmpty())return;
        changing=true;
        try{
            text.append(value);
            if(text.length()>80000){int remove=text.length()-65000;((Editable)text.getText()).delete(0,remove);anchor=Math.max(0,anchor-remove);}
        }finally{changing=false;}
        requestLayout();
    }
    void replaceText(String value,boolean latest){
        changing=true;
        try{
            if(latest){following=true;anchor=0;offset=0;}
            if(value.length()>80000)value=value.substring(value.length()-65000);
            text.setText(value,TextView.BufferType.EDITABLE);
        }finally{changing=false;}
        requestLayout();notifyFollow();
    }
    void latest(){following=true;requestLayout();notifyFollow();}
    Bundle saveViewport(){
        Bundle state=new Bundle();state.putBoolean("tail",following);state.putInt("anchor",anchor);state.putInt("offset",offset);return state;
    }
    void loadViewport(Bundle state){
        if(state==null)return;
        following=state.getBoolean("tail",true);anchor=state.getInt("anchor");offset=state.getInt("offset");requestLayout();notifyFollow();
    }
}
