import napari
from widgets import PiCamPlugin

def main():
    # 1. Create the Napari viewer
    viewer = napari.Viewer()
    
    # 2. Instantiate our Master Controller (which pulls in all the separated tabs)
    plugin_widget = PiCamPlugin(viewer)
    
    # 3. Dock the widget into the Napari window
    viewer.window.add_dock_widget(plugin_widget, name="Pi Control")
    
    # 4. Start the application loop
    napari.run()

if __name__ == "__main__":
    main()