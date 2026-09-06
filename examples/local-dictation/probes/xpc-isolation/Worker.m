#import "Protocol.h"
#import <Metal/Metal.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#include <errno.h>
@interface Worker : NSObject <ProbeProtocol, NSXPCListenerDelegate>
@end
@implementation Worker
- (void)probePort:(int)port handle:(NSFileHandle *)handle reply:(void (^)(NSString *))reply {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    int socketError = fd < 0 ? errno : 0;
    struct sockaddr_in addr = {0};
    addr.sin_len = sizeof(addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    int result = fd < 0 ? -1 : connect(fd, (struct sockaddr *)&addr, sizeof(addr));
    int connectError = result < 0 ? errno : 0;
    if (fd >= 0) close(fd);
    NSData *data = [handle readDataOfLength:5];
    BOOL handleOK = [data isEqualToData:[@"probe" dataUsingEncoding:NSUTF8StringEncoding]];
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    id<MTLBuffer> buffer = [device newBufferWithLength:4096 options:MTLResourceStorageModeShared];
    reply([NSString stringWithFormat:@"socket_errno=%d connect_result=%d connect_errno=%d handle_ok=%d metal_device=%d metal_buffer=%d", socketError, result, connectError, handleOK, device != nil, buffer != nil]);
}
- (BOOL)listener:(NSXPCListener *)listener shouldAcceptNewConnection:(NSXPCConnection *)connection {
    connection.exportedInterface = [NSXPCInterface interfaceWithProtocol:@protocol(ProbeProtocol)];
    connection.exportedObject = self;
    [connection resume];
    return YES;
}
@end
int main(void) {
    @autoreleasepool {
        Worker *worker = [Worker new];
        NSXPCListener *listener = [NSXPCListener serviceListener];
        listener.delegate = worker;
        [listener resume];
    }
    return 0;
}
