#import "Protocol.h"
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#include <errno.h>
int main(void) {
    @autoreleasepool {
        int server = socket(AF_INET, SOCK_STREAM, 0);
        struct sockaddr_in addr = {0};
        addr.sin_len = sizeof(addr);
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (server < 0 || bind(server, (struct sockaddr *)&addr, sizeof(addr)) || listen(server, 4)) {
            perror("listener"); return 2;
        }
        socklen_t size = sizeof(addr);
        getsockname(server, (struct sockaddr *)&addr, &size);
        int client = socket(AF_INET, SOCK_STREAM, 0);
        int control = connect(client, (struct sockaddr *)&addr, sizeof(addr));
        printf("unsandboxed_control_connect=%d errno=%d\n", control, control < 0 ? errno : 0);
        close(client);
        if (control != 0) return 3;
        NSString *service = [NSBundle.mainBundle.bundleIdentifier stringByAppendingString:@".Worker"];
        BOOL allowNetwork = [NSBundle.mainBundle.infoDictionary[@"ProbeAllowNetwork"] boolValue];
        NSXPCConnection *connection = [[NSXPCConnection alloc] initWithServiceName:service];
        connection.remoteObjectInterface = [NSXPCInterface interfaceWithProtocol:@protocol(ProbeProtocol)];
        [connection resume];
        dispatch_semaphore_t done = dispatch_semaphore_create(0);
        __block int status = 4;
        id<ProbeProtocol> proxy = [connection remoteObjectProxyWithErrorHandler:^(NSError *error) {
            fprintf(stderr, "XPC: %s\n", error.description.UTF8String);
            dispatch_semaphore_signal(done);
        }];
        int pipeFDs[2];
        if (pipe(pipeFDs)) return 7;
        write(pipeFDs[1], "probe", 5);
        close(pipeFDs[1]);
        NSFileHandle *handle = [[NSFileHandle alloc] initWithFileDescriptor:pipeFDs[0] closeOnDealloc:YES];
        [proxy probePort:ntohs(addr.sin_port) handle:handle reply:^(NSString *result) {
            printf("sandboxed_worker: %s\n", result.UTF8String);
            NSString *expected = allowNetwork
                ? @"connect_result=0 connect_errno=0 handle_ok=1 metal_device=1 metal_buffer=1"
                : @"connect_result=-1 connect_errno=1 handle_ok=1 metal_device=1 metal_buffer=1";
            status = [result containsString:expected] ? 0 : 5;
            dispatch_semaphore_signal(done);
        }];
        if (dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 15 * NSEC_PER_SEC))) {
            fprintf(stderr, "XPC reply timeout\n"); status = 6;
        }
        [connection invalidate];
        close(server);
        return status;
    }
}
