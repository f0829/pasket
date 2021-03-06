package android.widget;

public class RelativeLayout extends ViewGroup {
  public RelativeLayout(Context context);

  public static final int BELOW = 3;
  public static final int ALIGN_PARENT_BOTTOM = 12;
  public static final int CENTER_IN_PARENT = 13;
  public static final int CENTER_HORIZONTAL = 14;
  public static final int CENTER_VERTICAL = 15;

  public static class LayoutParams extends ViewGroup.MarginLayoutParams {
    public LayoutParams(int width, int height);

    public void addRule(int verb);
    public void addRule(int verb, int anchor);
  }

}
